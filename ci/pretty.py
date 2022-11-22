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

import csv
import functools
import sys
from typing import Callable

NA = "--:--"


def mark_if_clean(line: dict) -> dict:
  if line['build_type'].startswith("CLEAN "):
    line["description"] = "CLEAN " + line["description"]
    line["build_type"] = line["build_type"].replace("CLEAN ", "", 1)
  return line


def groupby(xs: list[dict], keyfn: Callable[[dict], str]) -> dict[
  str, list[dict]]:
  grouped = {}
  for x in xs:
    k = keyfn(x)
    grouped.setdefault(k, []).append(x)
  return grouped


def pretty_time(s):
  if s.startswith("0:"):
    s = s[2:]
  if s.startswith("0"):
    s = s[1:]
  if s.find(".") > 0:
    return s[0:s.find(".")]
  else:
    return s


def write_table(out, rows):
  def cell_width(prev, row):
    for i in range(len(row)):
      if len(prev) <= i:
        prev.append(0)
      prev[i] = max(prev[i], len(str(row[i])))
    return prev

  separators = ["-" * len(cell) for cell in rows[0]]
  rows.insert(1, separators)
  widths = functools.reduce(cell_width, rows, [])
  fmt = "  ".join([f"%-{width}s" for width in widths]) + "\n"
  for row in rows:
    out.write(fmt % tuple([str(cell) for cell in row]))


def pretty(filename):
  with open(filename) as f:
    lines = [mark_if_clean(line) for line in csv.DictReader(f) if
             not line['description'].startswith('rebuild-')]

  for line in lines:
    if line["build_result"] != "SUCCESS":
      print(f"{line['build_result']}: "
            f"{line['description']} / {line['build_type']}")

  by_cuj = groupby(lines, lambda l: l["description"])
  by_cuj_by_build_type = {
      k: groupby(v, lambda l: l["build_type"]) for k, v in
      by_cuj.items()}

  build_types = []
  for line in lines:
    build_type = line["build_type"]
    if build_type not in build_types:
      build_types.append(line["build_type"])

  rows = [["cuj", "build command"] + build_types]  # headers
  for cuj, by_build_type in by_cuj_by_build_type.items():
    targets = next(iter(by_build_type.values()))[0]["targets"]
    row = [cuj, f"m {targets}"]
    for build_type in build_types:
      lines = by_build_type.get(build_type)
      row.append(NA if not lines else pretty_time(lines[-1]['time']))
    rows.append(row)

  write_table(sys.stdout, rows)


if __name__ == "__main__":
  pretty(sys.argv[1])