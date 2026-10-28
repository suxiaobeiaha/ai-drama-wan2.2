#!/bin/bash
# parget.sh — parallel range downloader: PID-tracked children, per-part redo, instance lock
# NOTE: curl 8.12 rejects combining -C with -r, so failed parts are redownloaded whole.
# usage: parget.sh <url> <output_file> <total_size_bytes> [connections]
set -u
url="$1"; out="$2"; total="$3"; conns="${4:-8}"

# instance lock (prevents overlapping parget runs on the same output)
lockdir="$out.lock"
mkdir "$lockdir" 2>/dev/null || { echo "LOCKED: another parget is running for $out"; exit 99; }
trap 'rm -rf "$lockdir"' EXIT

# resume from existing complete prefix (written by single sequential curl -C - in a past life)
base=0
if [ -f "$out" ]; then
  base=$(stat -c%s "$out" 2>/dev/null || echo 0)
  [ "$base" -ge "$total" ] && { echo "ALREADY DONE: $out"; exit 0; }
fi
remain=$((total - base))

parts_dir="$out.parts"
mkdir -p "$parts_dir"
chunk=$(( (remain + conns - 1) / conns ))

# launch one tracked curl per range; incomplete parts are redone from scratch
pids=""
i=0
while [ $((i*chunk)) -lt "$remain" ]; do
  s=$((base + i*chunk))
  e=$((s + chunk - 1)); [ "$e" -ge "$total" ] && e=$((total-1))
  part="$parts_dir/p$(printf %02d "$i")"
  expected=$((e-s+1))
  actual=$(stat -c%s "$part" 2>/dev/null || echo 0)
  if [ "$actual" -ne "$expected" ]; then
    rm -f "$part" 2>/dev/null
    curl -sL --retry 20 --retry-delay 5 --retry-all-errors --connect-timeout 25 \
        --speed-time 45 --speed-limit 512 \
        -r "$s-$e" -o "$part" "$url" &
    pids="$pids $!"
  fi
  i=$((i+1))
done

for p in $pids; do wait "$p" 2>/dev/null; done

ok=1
i=0
while [ $((i*chunk)) -lt "$remain" ]; do
  s=$((base + i*chunk))
  e=$((s + chunk - 1)); [ "$e" -ge "$total" ] && e=$((total-1))
  part="$parts_dir/p$(printf %02d "$i")"
  expected=$((e-s+1))
  actual=$(stat -c%s "$part" 2>/dev/null || echo 0)
  if [ "$actual" -ne "$expected" ]; then ok=0; echo "part $part incomplete: $actual/$expected"; fi
  i=$((i+1))
done

if [ "$ok" -eq 1 ]; then
  i=0
  while [ $((i*chunk)) -lt "$remain" ]; do
    cat "$parts_dir/p$(printf %02d "$i")" >> "$out"
    i=$((i+1))
  done
  rm -rf "$parts_dir"
  final=$(stat -c%s "$out")
  echo "DONE: $out ($final bytes)"
  [ "$final" -eq "$total" ] || { echo "SIZE MISMATCH: expected $total"; exit 1; }
else
  echo "INCOMPLETE: rerun to retry failed parts"
  exit 1
fi
