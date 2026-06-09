#!/bin/bash
set -e
if [[ "$#" -ne 1 ]]; then
  echo "Usage: $0 <dest>" >&2
  exit 1
fi
dest="$(realpath "$1")"
cd "$(dirname "$0")/.."

main() {
  if [[ -e "$dest/_transmitter" ]]; then
    install-xmitter
  else
    install-recvr
  fi
}

install-recvr() {
  local files=(
    classes.py
    code.py
    defaults.py
    utils.py
  )
  for file in "${files[@]}"; do
    copy-update "$file" "$dest/$file"
  done
  cp -X -v -n \
    settings.py \
    "$dest" || true
  cp -X -v -n -r \
    lib/* \
    "$dest/lib/" || true
}

install-xmitter() {
  local files=(
    utils.py
  )
  for file in "${files[@]}"; do
    copy-update "$file" "$dest/$file"
  done
  cp -X -v -n \
    settings.py \
    "$dest" || true
  pushd xmitter > /dev/null
  files=(
    code.py
    defaults.py
  )
  for file in "${files[@]}"; do
    copy-update "$file" "$dest/$file"
  done
  cp -X -v -n -r \
    lib/* \
    "$dest/lib/" || true
  popd > /dev/null
}

copy-update() {
  local src="$1"
  local tgt="$2"
  if files-equal "$src" "$tgt"; then
    echo "no change: $src"
  else
    cp -X -v "$src" "$tgt"
  fi
}

files-equal() {
  local a="$1"
  local b="$2"
  if [[ -e "$a" ]] && [[ -e "$b" ]] && [[ "$(md5sum "$a" | awk '{print $1}')" == "$(md5sum "$b" | awk '{print $1}')" ]]; then
    return 0
  else
    return 1
  fi
}

main