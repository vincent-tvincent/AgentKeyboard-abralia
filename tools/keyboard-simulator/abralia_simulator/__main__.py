# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Start the independent simulator or export its narrated scenarios."""

import argparse
from concurrent.futures import ProcessPoolExecutor
import importlib.util
import json
from pathlib import Path

from .engine import Simulator, DEFAULT_PROFILE


def _frame_plugin(path):
    path=Path(path).resolve()
    if path.suffix!='.py' or not path.is_file(): raise ValueError('plugin must be a local Python file')
    spec=importlib.util.spec_from_file_location('abralia_local_simulator_design',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    callback=getattr(module,'frame',None)
    if not callable(callback): raise ValueError('plugin must define frame(elapsed, profile, state)')
    return callback


def _export(arguments):
    from .export import export_scenario
    return export_scenario(**arguments)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    serve=commands.add_parser('serve')
    serve.add_argument('--profile',default=DEFAULT_PROFILE)
    serve.add_argument('--port',type=int,default=0)
    serve.add_argument('--design',type=Path)
    serve.add_argument('--plugin',type=Path,help='trusted local Python frame callback; never accepted through HTTP')
    serve.add_argument('--scenario')
    serve.add_argument('--seed',type=int,default=1)
    export=commands.add_parser('export')
    choice=export.add_mutually_exclusive_group()
    choice.add_argument('--scenario'); choice.add_argument('--all',action='store_true')
    choice.add_argument('--design',type=Path)
    export.add_argument('--plugin',type=Path,help='trusted local frame(elapsed, profile, state) callback')
    export.add_argument('--duration',type=float,default=10)
    export.add_argument('--output',type=Path,required=True)
    export.add_argument('--profile',default=DEFAULT_PROFILE)
    export.add_argument('--fps',type=int,default=12)
    export.add_argument('--width',type=int,default=960)
    export.add_argument('--jobs',type=int,default=1)
    args=parser.parse_args(argv)
    try:
        if args.command=='export':
            from .scenarios import SCENARIOS
            if not 1<=args.jobs<=16: raise ValueError('jobs must be between 1 and 16')
            if args.design or args.plugin:
                if args.scenario or args.all: raise ValueError('custom design/plugin cannot be combined with tutorial scenarios')
                from .export import export_design
                if args.design and args.design.stat().st_size>1024*1024: raise ValueError('design exceeds one megabyte')
                design=json.loads(args.design.read_text()) if args.design else {'version':1,'name':'Python frame design'}
                result=export_design(design,str(args.output),profile=args.profile,duration=args.duration,
                    fps=args.fps,width=args.width,frame_callback=_frame_plugin(args.plugin) if args.plugin else None)
                print(json.dumps(result,default=str))
                return 0
            if not args.all and not args.scenario: raise ValueError('choose --scenario, --all, --design or --plugin')
            names=list(SCENARIOS) if args.all else [args.scenario]
            if any(name not in SCENARIOS for name in names): raise ValueError('unknown scenario')
            if args.all: args.output.mkdir(parents=True,exist_ok=True)
            tasks=[{'name':name,'output':str(args.output/(name+'.gif') if args.all else args.output),
                    'profile':args.profile,'fps':args.fps,'width':args.width} for name in names]
            if args.jobs==1: results=[_export(task) for task in tasks]
            else:
                with ProcessPoolExecutor(max_workers=min(args.jobs,len(tasks))) as pool: results=list(pool.map(_export,tasks))
            print(json.dumps(results,default=str))
            return 0
        callback=_frame_plugin(args.plugin) if args.plugin else None
        simulator=Simulator(args.profile,seed=args.seed,frame_callback=callback)
        if args.design:
            if args.design.stat().st_size>1024*1024: raise ValueError('design exceeds one megabyte')
            simulator.load_design(json.loads(args.design.read_text()))
        if args.scenario: simulator.load_scenario(args.scenario)
        from .server import create_server
        server=create_server(simulator,port=args.port)
        print(f'Abralia simulator: http://127.0.0.1:{server.server_port}',flush=True)
        print('Virtual keyboard only. Ctrl-C stops this server.',flush=True)
        try: server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt: pass
        finally: server.server_close()
        return 0
    except (ValueError,TypeError,OSError) as error:
        parser.exit(2,str(error)+'\n')


if __name__=='__main__':
    raise SystemExit(main())
