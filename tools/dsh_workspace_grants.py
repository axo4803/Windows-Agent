#!/usr/bin/env python
"""Inventory and repair the standing Windows security edits DSH leaves on a workspace.

DSH's Windows ACL sandbox keeps a workspace usable by confined children with
three standing edits on the workspace root, which it never revokes:

  * an allow ACE for a capability SID,
  * a deny of FILE_DELETE_CHILD to Everyone, and
  * an inheritable Low integrity label.

The label is inherited by everything under the tree, so an executable stored
there - a project's ``.venv\\Scripts\\python.exe``, for example - starts at Low
integrity. Windows refuses write-class access from a Low process to a
Medium-labelled object, so that Python cannot write ``%TEMP%`` and silently
falls back to the current directory. Medium-integrity programs launched against
that tree can be denied access to files and profile directories inside it.

Nothing in DSH undoes this: ``dispose()`` keeps the standing workspace pair by
design, and ``icacls`` cannot express an integrity label at all.

Usage
-----
    python tools/dsh_workspace_grants.py report [path ...] [--tree]
    python tools/dsh_workspace_grants.py revoke <path> [--apply]
    python tools/dsh_workspace_grants.py selftest

``report`` is read-only and exits non-zero when it finds the damaging edits, so
it can be used as a check. ``revoke`` rewrites security descriptors, so it only
prints what it would do unless ``--apply`` is passed, and it always writes an
SDDL backup first. ``selftest`` proves both against a throwaway directory.

Needs pywin32 (``win32security``) in the environment that runs it.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import win32security
except ImportError:  # pragma: no cover - environment guard
    print('This tool needs pywin32 (win32security) installed in the running environment.')
    raise SystemExit(2)

SE_FILE_OBJECT = 1
DACL_SI = 0x00000004
LABEL_SI = 0x00000010
SDDL_REVISION_1 = 1

WORLD = 'S-1-1-0'
LOW_SID = 'S-1-16-4096'
MEDIUM_SID = 'S-1-16-8192'
INTEGRITY_NAMES = {'S-1-16-0': 'Untrusted', 'S-1-16-4096': 'Low', 'S-1-16-8192': 'Medium',
                   'S-1-16-12288': 'High', 'S-1-16-16384': 'System', 'S-1-16-20480': 'Protected'}
MANDATORY_LABEL_ACE_TYPE = 17
ACCESS_DENIED_ACE_TYPE = 1
FILE_DELETE_CHILD = 0x40

#: SID prefixes DSH uses for its own grants; informational, not damage.
DSH_SID_PREFIXES = ('S-1-4-', 'S-1-15-')


def sid_string(sid) -> str:
    try:
        return win32security.ConvertSidToStringSid(sid)
    except Exception:
        return repr(sid)


def inspect(path) -> dict:
    """Read one object's DACL denies, DSH-related allows, and integrity label."""
    sd = win32security.GetNamedSecurityInfo(str(path), SE_FILE_OBJECT, DACL_SI | LABEL_SI)
    denies, dsh_allows = [], []
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl:
        for index in range(dacl.GetAceCount()):
            (ace_type, flags), mask, sid = dacl.GetAce(index)
            entry = (sid_string(sid), mask, flags)
            if ace_type == ACCESS_DENIED_ACE_TYPE:
                denies.append(entry)
            elif entry[0].startswith(DSH_SID_PREFIXES):
                dsh_allows.append(entry)
    label = None
    sacl = sd.GetSecurityDescriptorSacl()
    if sacl:
        for index in range(sacl.GetAceCount()):
            (ace_type, _flags), _mask, sid = sacl.GetAce(index)
            if ace_type == MANDATORY_LABEL_ACE_TYPE:
                label = sid_string(sid)
    return {'path': Path(path), 'denies': denies, 'dsh_allows': dsh_allows, 'label': label}


def problems(observation: dict) -> list:
    """The edits that actually break programs living in the tree."""
    found = []
    if observation['label'] == LOW_SID:
        found.append('low_integrity_label')
    if any(sid == WORLD for sid, _mask, _flags in observation['denies']):
        found.append('everyone_deny')
    return found


def canary() -> tuple:
    """Can a program running from this tree write %TEMP%? False means Low integrity."""
    target = os.environ.get('TEMP') or tempfile.gettempdir()
    probe = os.path.join(target, 'dsh-grants-canary.txt')
    try:
        with open(probe, 'w', encoding='utf-8') as handle:
            handle.write('x')
        os.remove(probe)
        return True, target
    except OSError as exc:
        return False, '%s (%s)' % (target, type(exc).__name__)


def ancestors(path: Path) -> list:
    chain, current = [], path.resolve()
    while True:
        chain.append(current)
        if current.parent == current:
            return chain
        current = current.parent


def descendants(path: Path, limit: int = 2000) -> list:
    found = []
    for current, directories, _files in os.walk(path):
        for name in directories:
            found.append(Path(current) / name)
            if len(found) >= limit:
                return found, True
    return found, False


def describe(observation: dict) -> str:
    label = INTEGRITY_NAMES.get(observation['label'], observation['label'] or 'none')
    deny_note = ', '.join(sorted({sid for sid, _m, _f in observation['denies']})) or 'none'
    return 'label=%-8s denies=%s' % (label, deny_note)


def command_report(args) -> int:
    targets = [Path(p).resolve() for p in args.paths]
    if args.ancestors:
        for target in list(targets):
            targets.extend(a for a in ancestors(target) if a not in targets)
    if args.tree:
        for target in list(targets):
            extra, truncated = descendants(target)
            targets.extend(p for p in extra if p not in targets)
            if truncated:
                print('note: subtree scan truncated; re-run on a deeper path if the verdict is clean')
    broken = []
    truncated = False
    for target in targets:
        try:
            observation = inspect(target)
        except OSError as exc:
            print('  %-60s unreadable (%s)' % (target, type(exc).__name__))
            continue
        found = problems(observation)
        marker = 'BROKEN' if found else 'ok    '
        print('  %s %-58s %s' % (marker, str(target)[-58:], describe(observation)))
        for sid, mask, _flags in observation['dsh_allows']:
            print('         dsh grant (expected): %s mask=%#010x' % (sid, mask))
        if found:
            broken.append((target, found))
    writable, detail = canary()
    print('\n  canary: %s to write %%TEMP%% -> %s' % ('ABLE' if writable else 'UNABLE', detail))
    if broken or not writable:
        print('\nVERDICT: DSH workspace edits are present (or their effect is).')
        for target, found in broken:
            print('  %s: %s' % (target, ', '.join(found)))
        print('  Repair:  python %s revoke "%s" --apply' % (Path(__file__).name, broken[0][0] if broken else targets[0]))
        return 1
    print('\nVERDICT: clean - no Low integrity label and no Everyone deny found.')
    return 0


def backup_descriptor(path: Path, directory: Path) -> Path:
    sd = win32security.GetNamedSecurityInfo(str(path), SE_FILE_OBJECT, DACL_SI | LABEL_SI)
    sddl = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(sd, SDDL_REVISION_1, DACL_SI | LABEL_SI)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    out = directory / ('dsh-grants-%s-%s.sddl' % (path.name or 'root', stamp))
    out.write_text(sddl + '\n', encoding='utf-8')
    return out


def set_integrity_label(path: Path, level: str = 'ME') -> None:
    """Set the mandatory label. `ME` restores the ordinary Medium label."""
    sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor('S:(ML;;NW;;;%s)' % level, SDDL_REVISION_1)
    sacl = sd.GetSecurityDescriptorSacl()
    win32security.SetNamedSecurityInfo(str(path), SE_FILE_OBJECT, LABEL_SI, None, None, None, sacl)


def remove_world_denies(path: Path) -> tuple:
    """Drop Everyone deny ACEs with icacls, which accepts a literal SID."""
    result = subprocess.run(['icacls', str(path), '/remove:d', '*' + WORLD],
                            capture_output=True, text=True)
    return result.returncode == 0, (result.stdout or result.stderr or '').strip()


def command_revoke(args) -> int:
    target = Path(args.path).resolve()
    observation = inspect(target)
    found = problems(observation)
    if not found:
        print('Nothing to repair on %s (%s).' % (target, describe(observation)))
        return 0
    backup = backup_descriptor(target, Path(args.backup_dir).resolve())
    print('  backup of the current descriptor: %s' % backup)
    print('  found: %s' % ', '.join(found))
    if not args.apply:
        print('\n  dry run. Would:')
        if 'everyone_deny' in found:
            print('    - remove Everyone deny ACEs with: icacls "%s" /remove:d *%s' % (target, WORLD))
        if 'low_integrity_label' in found:
            print('    - reset the integrity label from Low to Medium')
        print('\n  re-run with --apply to make those changes.')
        return 0
    if 'everyone_deny' in found:
        ok, detail = remove_world_denies(target)
        print('  %s remove Everyone deny ACEs' % ('removed' if ok else 'FAILED to remove'))
        if not ok:
            print('    %s' % detail[:300])
    if 'low_integrity_label' in found:
        try:
            set_integrity_label(target, 'ME')
            print('  reset integrity label to Medium')
        except Exception as exc:
            print('  FAILED to reset the label: %s: %s' % (type(exc).__name__, exc))
            print('    the label lives in the SACL, so this needs WRITE_OWNER on the object')
    after = inspect(target)
    remaining = problems(after)
    writable, detail = canary()
    print('\n  after: %s' % describe(after))
    print('  canary: %s to write %%TEMP%% -> %s' % ('ABLE' if writable else 'UNABLE', detail))
    if remaining:
        print('VERDICT: still broken (%s)' % ', '.join(remaining))
        return 1
    print('VERDICT: repaired. Descendants that inherited the old label may need the same call.')
    print('  restore if needed: build a descriptor from %s' % backup)
    return 0


def command_selftest(_args) -> int:
    """Apply a DSH-shaped state to a throwaway tree, then detect and repair it."""
    base = Path(tempfile.mkdtemp(prefix='dsh-grants-selftest-'))
    tree = base / 'tree'
    results = []
    try:
        tree.mkdir()
        (tree / 'marker.txt').write_text('x', encoding='utf-8')
        applied, detail = remove_world_denies(tree)  # no-op warm-up keeps icacls happy
        result = subprocess.run(['icacls', str(tree), '/deny', '*%s:(DC)' % WORLD],
                                capture_output=True, text=True)
        results.append(('apply Everyone deny', result.returncode == 0))
        try:
            set_integrity_label(tree, 'LW')
            results.append(('apply Low integrity label', True))
        except Exception as exc:
            results.append(('apply Low integrity label', False))
            print('  could not set the label: %s: %s' % (type(exc).__name__, exc))
            print('  (this is the same WRITE_OWNER requirement DSH documents)')
        detected = problems(inspect(tree))
        results.append(('detect both edits', set(detected) == {'low_integrity_label', 'everyone_deny'}))
        command_revoke(argparse.Namespace(path=str(tree), apply=True, backup_dir=str(base / 'backups')))
        after = problems(inspect(tree))
        results.append(('verify repaired', after == []))
    finally:
        for attempt in range(3):
            try:
                shutil.rmtree(base, ignore_errors=False)
                break
            except OSError:
                time.sleep(0.5)
    print('\nSELFTEST')
    for name, ok in results:
        print('  %-28s %s' % (name, 'PASS' if ok else 'FAIL'))
    return 0 if all(ok for _name, ok in results) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command')

    report = sub.add_parser('report', help='read-only inventory (exit 1 when damaged)')
    report.add_argument('paths', nargs='*', default=['.'], help='paths to inspect (default: .)')
    report.add_argument('--tree', action='store_true', help='walk descendants too (bounded)')
    report.add_argument('--no-ancestors', dest='ancestors', action='store_false',
                        help='inspect only the named paths, not their ancestors')
    report.set_defaults(func=command_report)

    revoke = sub.add_parser('revoke', help='remove the damaging edits')
    revoke.add_argument('path')
    revoke.add_argument('--apply', action='store_true', help='actually change security descriptors')
    revoke.add_argument('--backup-dir', default='.', help='where to write the SDDL backup')
    revoke.set_defaults(func=command_revoke)

    selftest = sub.add_parser('selftest', help='prove detection and repair on a throwaway tree')
    selftest.set_defaults(func=command_selftest)

    args = parser.parse_args(argv)
    if not getattr(args, 'func', None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
