#!/usr/bin/env python3

# Copyright (C) 2022 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
A tool for running builds (soong or b) and measuring the time taken.
"""

import datetime
import functools
import logging
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Final
from typing import Mapping

import util
import cuj_catalog
import perf_metrics
import ui

MAX_RUN_COUNT: Final[int] = 5


@functools.cache
def _prepare_env() -> (Mapping[str, str], str):
  def get_soong_build_ninja_args():
    ninja_args = os.environ.get('NINJA_ARGS') or ''
    if ninja_args != '':
      ninja_args += ' '
    ninja_args += '-d explain --quiet'
    if util.is_ninja_dry_run(ninja_args):
      logging.warning(f'Running dry ninja runs NINJA_ARGS={ninja_args}')
    return ninja_args

  def get_soong_ui_ninja_args():
    soong_ui_ninja_args = os.environ.get('SOONG_UI_NINJA_ARGS') or ''
    if util.is_ninja_dry_run(soong_ui_ninja_args):
      sys.exit('"-n" in SOONG_UI_NINJA_ARGS would not update build.ninja etc')

    if soong_ui_ninja_args != '':
      soong_ui_ninja_args += ' '
    soong_ui_ninja_args += '-d explain --quiet'
    return soong_ui_ninja_args

  overrides: Mapping[str, str] = {
      'NINJA_ARGS': get_soong_build_ninja_args(),
      'SOONG_UI_NINJA_ARGS': get_soong_ui_ninja_args()
  }
  env = {**os.environ, **overrides}
  if not os.environ.get('TARGET_BUILD_PRODUCT'):
    env['TARGET_BUILD_PRODUCT'] = 'aosp_arm64'
    env['TARGET_BUILD_VARIANT'] = 'userdebug'

  pretty_env_str = [f'{k}={v}' for (k, v) in env.items()]
  pretty_env_str.sort()
  return env, '\n'.join(pretty_env_str)


def _build(user_input: ui.UserInput) -> (int, dict[str, any]):
  if not hasattr(_build, 'logfile'):
    _build.logfile = util.next_file(user_input.log_dir.joinpath('output.log'))
  logfile = next(_build.logfile)
  logging.info('TIP: to see the log:\n  tail -f "%s"', logfile)
  cmd = [*user_input.build_type.value, *user_input.targets]
  logging.info('Command: %s', cmd)
  env, env_str = _prepare_env()
  with open(logfile, mode='w') as f:
    f.write(f'Command: {cmd}\n')
    f.write(f'Environment Variables:\n{textwrap.indent(env_str, "  ")}\n\n\n')
    f.flush()
    start_ns = time.perf_counter_ns()
    p = subprocess.run(cmd, check=False, cwd=util.get_top_dir(), env=env,
                       shell=False, stdout=f, stderr=f)
    elapsed_ns = time.perf_counter_ns() - start_ns

  build_type = user_input.build_type.name.lower()
  return (p.returncode, {
      'build_type': build_type,
      'targets': ' '.join(user_input.targets),
      'log': logfile.relative_to(user_input.log_dir),
      'ninja_explains': util.count_explanations(logfile),
      'time': datetime.timedelta(microseconds=elapsed_ns / 1000)
  })


def main():
  """
  Run provided target(s) under various CUJs and collect metrics.
  In pseudocode:
    time build <target> with m or b
    collect metrics
    for each cuj:
        make relevant changes
        time rebuild
        collect metrics
        revert those changes
        time rebuild
        collect metrics
  """
  user_input = ui.handle_user_input()

  logging.warning(textwrap.dedent('''
  If you kill this process, make sure to revert unwanted changes.
  TIP: If you have no local changes of interest you may
       `repo forall -p -c git reset --hard`  and
       `repo forall -p -c git clean --force` and even
       `m clean && rm -rf out`
  '''))

  clean = not util.get_out_dir().joinpath('soong/bootstrap.ninja').exists()
  summary_csv_path: Final[Path] = user_input.log_dir.joinpath(util.SUMMARY_CSV)
  for counter, cuj_index in enumerate(user_input.chosen_cujgroups):
    cujgroup = cuj_catalog.get_cujgroups()[cuj_index]
    logging.info('START %s [%d out of %d]', cujgroup.description, counter + 1,
                 len(user_input.chosen_cujgroups))
    for cujstep in cujgroup.steps:
      desc = f'{cujstep.verb} {cujgroup.description}'
      logging.info('START %s', desc)
      cujstep.action()
      run = 0
      while True:
        # build
        (exit_code, build_info) = _build(user_input)
        # collect perf metrics
        perf = perf_metrics.read(user_input.log_dir)
        # if build was successful, run test
        if exit_code != 0:
          build_result = cuj_catalog.BuildResult.FAILED.name
        else:
          try:
            cujstep.verify(user_input)
            build_result = cuj_catalog.BuildResult.SUCCESS.name
          except Exception as e:
            logging.error(e)
            build_result = (cuj_catalog.BuildResult.TEST_FAILURE.name +
                            ':' + str(e))
        # summarize
        log_desc = desc if run == 0 else f'rebuild-{run} after {desc}'
        build_info = {
                         'description': log_desc,
                         'build_result': build_result
                     } | build_info
        logging.info('%s after %s: %s',
                     build_info["build_result"], build_info["time"], log_desc)
        if clean:
          build_info['build_type'] = 'CLEAN ' + build_info['build_type']
          clean = False  # we don't clean subsequently
        perf_metrics.write_csv_row(summary_csv_path, build_info | perf)
        if util.is_ninja_dry_run() or run > MAX_RUN_COUNT or build_info[
          'ninja_explains'] == 0:
          # dry run or build has stabilized
          break
        run += 1
      logging.info(' DONE %s', desc)

  summary_cmd = util.get_summary_cmd(user_input.log_dir)
  subprocess.run(summary_cmd, shell=True)
  logging.info('TIP: ' + summary_cmd)


if __name__ == '__main__':
  logging.root.setLevel(logging.INFO)
  f = logging.Formatter('%(levelname)s: %(message)s')
  for h in logging.root.handlers:
    h.setFormatter(f)
  main()