#!/bin/bash
# perch2api → http://127.0.0.1:12066
cd "$(dirname "$0")" || exit 1
export -n PYTHONHOME PYTHONPATH 2>/dev/null; unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH
mkdir -p data
exec env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH \
  python3 server.py 2>&1 | tee -a data/bridge.log
