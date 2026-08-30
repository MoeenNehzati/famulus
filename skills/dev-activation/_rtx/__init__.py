import argparse
import json
import os
import sys
from officina.runtime.python_machine_interface import PythonMachineInterface
from . import _development_activation as activation
class Interface(PythonMachineInterface):
    prog = "dev-activation"
    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("action", choices=("create", "validate", "report"))
        parser.add_argument("--checkout", required=True)
        parser.add_argument("--platform", default=sys.platform)
        return parser
    def run(self, args: argparse.Namespace) -> int:
        result = activation.run_action(args.action, args.checkout, environ=os.environ, platform=args.platform)
        if args.action == "report":
            print(json.dumps(result, ensure_ascii=False))
        return 0
