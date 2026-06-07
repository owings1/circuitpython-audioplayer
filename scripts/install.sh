#!/bin/bash
set -e
if [[ "$#" -ne 1 ]]; then
  echo "Usage: $0 <dest>" >&2
  exit 1
fi
dest="$(realpath "$1")"
cd "$(dirname "$0")/.."
files=(
  classes.py
  code.py
  defaults.py
  utils.py
)
for file in "${files[@]}"; do
  if [[ -e "$dest/$file" ]] && [[ "$(md5sum "$file" | awk '{print $1}')" == "$(md5sum "$dest/$file" | awk '{print $1}')" ]] ; then
    echo "no change: $file"
  else
    cp -X -v "$file" "$dest/$file"
  fi
done
cp -X -v -n \
  settings.py \
  "$dest" || true
cp -X -v -n -r \
  lib/* \
  "$dest/lib/" || true