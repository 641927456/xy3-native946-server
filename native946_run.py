"""Run project scripts/tests with the isolated, pinned SQLite runtime."""
from pathlib import Path
import runpy,sys
from native946_sqlite_runtime import bootstrap

bootstrap(strict=True)
if len(sys.argv)<2:raise SystemExit('Usage: native946_run.py script.py [args] | -m module [args]')
arguments=sys.argv[1:]
if arguments[0]=='-m':
    if len(arguments)<2:raise SystemExit('Missing module name')
    sys.argv=arguments[1:]
    runpy.run_module(arguments[1],run_name='__main__',alter_sys=True)
else:
    target=(Path(__file__).resolve().parent/arguments[0]).resolve()
    if not target.is_file():raise SystemExit('Script not found')
    sys.argv=[str(target),*arguments[1:]]
    runpy.run_path(str(target),run_name='__main__')
