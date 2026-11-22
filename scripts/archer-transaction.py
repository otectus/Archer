#!/usr/bin/env python3
"""Create and advance durable, reviewable Archer install transactions."""

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

DEFAULT_ROOT = Path(os.environ.get('ARCHER_TRANSACTION_ROOT', '/var/lib/archer/transactions'))
STATES = (
    'planned', 'preflight-complete', 'applying', 'pending-restart',
    'verifying', 'committed', 'failed', 'recovery-required',
)
TRANSITIONS = {
    'planned': {'preflight-complete', 'failed'},
    'preflight-complete': {'applying', 'failed'},
    'applying': {'pending-restart', 'verifying', 'failed', 'recovery-required'},
    'pending-restart': {'verifying', 'failed', 'recovery-required'},
    'verifying': {'committed', 'failed', 'recovery-required'},
    'failed': {'preflight-complete', 'recovery-required'},
    'recovery-required': {'preflight-complete'},
    'committed': set(),
}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.archer-transaction-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load(path):
    data = json.loads(path.read_text())
    if data.get('schema') != 1 or data.get('state') not in STATES:
        raise ValueError('Unsupported transaction journal')
    if not isinstance(data.get('plan'), dict) or not isinstance(data.get('steps'), list):
        raise ValueError('Malformed transaction journal')
    return data


def current_path(root):
    pointer = root / 'current'
    if not pointer.exists():
        raise ValueError('No current Archer transaction')
    transaction_id = json.loads(pointer.read_text())
    if not transaction_id or '/' in transaction_id or transaction_id.startswith('.'):
        raise ValueError('Invalid transaction pointer')
    return root / f'{transaction_id}.json'


def create(root, plan_path, approval, kind, resume, supersede):
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    if not isinstance(plan, dict) or not isinstance(plan.get('modules'), list):
        raise ValueError('Plan must contain a modules array')
    root.mkdir(parents=True, exist_ok=True)
    pointer = root / 'current'
    if pointer.exists():
        old = load(current_path(root))
        if old['state'] not in ('committed',):
            if (supersede and kind == 'uninstall' and old['kind'] == 'install'
                    and old['state'] in ('pending-restart', 'failed', 'recovery-required')):
                old['state'] = 'recovery-required'
                old['updated_at'] = now()
                old['recovery'].append({
                    'at': now(),
                    'instruction': 'Superseded by an ownership-aware uninstall transaction.',
                })
                atomic(root / f"{old['id']}.json", old)
            elif not resume or old['kind'] != kind or old['state'] not in ('failed', 'recovery-required'):
                raise ValueError(
                    f"Transaction {old['id']} is {old['state']}; inspect/recover it before starting another"
                )
            else:
                old['steps'].append({'at': now(), 'name': 'retry', 'from': old['state'],
                                     'to': 'planned', 'result': 'ok',
                                     'message': 'A newly reviewed plan replaced the failed plan.'})
                old['state'] = 'planned'
                old['updated_at'] = now()
                old['approval'] = {'mode': approval, 'at': now()}
                old['plan_sha256'] = hashlib.sha256(plan_bytes).hexdigest()
                old['plan'] = plan
                old['recovery'] = []
                atomic(root / f"{old['id']}.json", old)
                print(old['id'])
                return
    transaction_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    data = {
        'schema': 1,
        'id': transaction_id,
        'kind': kind,
        'state': 'planned',
        'created_at': now(),
        'updated_at': now(),
        'approval': {'mode': approval, 'at': now()},
        'plan_sha256': hashlib.sha256(plan_bytes).hexdigest(),
        'plan': plan,
        'steps': [],
        'recovery': [],
    }
    atomic(root / f'{transaction_id}.json', data)
    atomic(pointer, transaction_id)
    print(transaction_id)


def update(root, target, message):
    path = current_path(root)
    data = load(path)
    if target not in TRANSITIONS[data['state']]:
        raise ValueError(f"Invalid transaction transition {data['state']} -> {target}")
    previous = data['state']
    data['state'] = target
    data['updated_at'] = now()
    data['steps'].append({
        'at': now(), 'name': 'state', 'from': previous, 'to': target,
        'result': 'ok' if target not in ('failed', 'recovery-required') else target,
        'message': message,
    })
    if target == 'recovery-required' and message:
        data['recovery'].append({'at': now(), 'instruction': message})
    atomic(path, data)


def record(root, name, result, message, resources):
    path = current_path(root)
    data = load(path)
    if data['state'] not in ('preflight-complete', 'applying', 'pending-restart', 'verifying'):
        raise ValueError(f"Cannot record a mutation step while transaction is {data['state']}")
    data['steps'].append({
        'at': now(), 'name': name, 'result': result, 'message': message,
        'resources': resources,
    })
    data['updated_at'] = now()
    atomic(path, data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest='action', required=True)
    begin = commands.add_parser('begin')
    begin.add_argument('plan', type=Path)
    begin.add_argument('--approval', choices=('interactive', 'no-confirm'), required=True)
    begin.add_argument('--kind', choices=('install', 'uninstall'), default='install')
    begin.add_argument('--resume', action='store_true', help='retry a failed transaction of the same kind')
    begin.add_argument('--supersede', action='store_true', help='start uninstall as recovery for an incomplete install')
    transition = commands.add_parser('transition')
    transition.add_argument('state', choices=STATES)
    transition.add_argument('--message', default='')
    step = commands.add_parser('step')
    step.add_argument('name')
    step.add_argument('result', choices=('ok', 'failed', 'skipped'))
    step.add_argument('--message', default='')
    step.add_argument('--resource', action='append', default=[])
    recover = commands.add_parser('recover')
    recover.add_argument('--message', required=True)
    commands.add_parser('show')
    args = parser.parse_args(argv)
    args.root.mkdir(parents=True, exist_ok=True)
    directory = os.open(args.root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        if args.action == 'begin':
            create(args.root, args.plan, args.approval, args.kind, args.resume, args.supersede)
        elif args.action == 'transition':
            update(args.root, args.state, args.message)
        elif args.action == 'step':
            record(args.root, args.name, args.result, args.message, args.resource)
        elif args.action == 'recover':
            path = current_path(args.root)
            data = load(path)
            if data['state'] not in ('applying', 'pending-restart', 'failed'):
                raise ValueError(f"Transaction {data['id']} is {data['state']}; recovery is not applicable")
            if 'recovery-required' not in TRANSITIONS[data['state']]:
                # Failed is deliberately promoted through its explicit recovery path.
                data['state'] = 'recovery-required'
                data['updated_at'] = now()
                data['recovery'].append({'at': now(), 'instruction': args.message})
                atomic(path, data)
            else:
                update(args.root, 'recovery-required', args.message)
        else:
            print(json.dumps(load(current_path(args.root)), indent=2, sort_keys=True))
    finally:
        os.close(directory)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f'Transaction refused: {error}')
