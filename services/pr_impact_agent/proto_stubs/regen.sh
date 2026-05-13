#!/usr/bin/env bash
# Regenerate the Python gRPC stubs from odin_deployer.proto.
# Run from the pr_impact_agent service root:
#   bash proto_stubs/regen.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SERVICE_ROOT="$(cd "$HERE/.." && pwd)"

"$SERVICE_ROOT/.venv/bin/python" -m grpc_tools.protoc \
  -I "$HERE" \
  --python_out="$HERE" \
  --grpc_python_out="$HERE" \
  "$HERE/odin_deployer.proto"

# protoc emits a flat `import odin_deployer_pb2` in the _grpc.py module,
# which breaks when the stubs live inside a Python package. Rewrite it
# to a package-relative import so the imports resolve under `from
# .proto_stubs import ...`.
case "$(uname -s)" in
  Darwin) SED_INPLACE=(-i ""); ;;
  *)      SED_INPLACE=(-i);     ;;
esac
sed "${SED_INPLACE[@]}" \
  's/^import odin_deployer_pb2 as/from . import odin_deployer_pb2 as/' \
  "$HERE/odin_deployer_pb2_grpc.py"

echo "regenerated: $HERE/odin_deployer_pb2.py + odin_deployer_pb2_grpc.py"
