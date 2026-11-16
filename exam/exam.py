#!/usr/bin/env python3
"""Exam: can a model that has never seen Dovod write it from SPEC.md alone?

For each task: model gets SPEC + task, writes a file; we run `dovod check`; model sees the
diagnostics and retries (max ATTEMPTS). Once check passes we run hidden tests the model never saw.
Records: attempts to pass check, own-example failures caught along the way (bugs the language's
evidence rule caught), hidden-test result, use of `assume`, unreached-block warnings.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOVOD = os.path.join(ROOT, 'bin', 'dovod')
MODEL = os.environ.get('EXAM_MODEL', 'glm-5.3:cloud')
URL = os.environ.get('EXAM_URL', 'http://localhost:11434/api/chat')
ATTEMPTS = int(os.environ.get('EXAM_ATTEMPTS', '5'))

SYSTEM = """You are writing code in Dovod, a new programming language you have never seen before.
Its complete specification is below. Write only what the specification allows.

Answer with ONE fenced code block containing the complete .dv file. No `main` unless the task asks for it.
Give every pure function good examples (`ex`): they are your tests, and they are run.

=== SPEC.md ===
""" + open(os.path.join(ROOT, 'SPEC.md'), encoding='utf-8').read()

TASKS = json.load(open(os.path.join(HERE, 'tasks.json'), encoding='utf-8'))


def ask(messages):
    body = json.dumps({'model': MODEL, 'messages': messages, 'stream': False,
                       'options': {'temperature': 0.2}}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL, body, {'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=900) as r:
                return json.load(r)['message']['content']
        except Exception as ex:  # network hiccup: retry
            err = ex
            time.sleep(10)
    raise SystemExit(f"model call failed: {err}")


def extract(reply):
    blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", reply, re.S)
    if not blocks:
        return reply.strip()
    return max(blocks, key=len)


def check(path):
    r = subprocess.run([DOVOD, 'check', path, '--json'], capture_output=True, text=True, timeout=300)
    try:
        return json.loads(r.stdout), r.stdout
    except json.JSONDecodeError:
        return {'ok': False, 'errors': [{'code': 'E-INTERNAL', 'msg': (r.stdout + r.stderr)[-2000:]}],
                'warnings': [], 'functions': []}, r.stdout + r.stderr


def human(path):
    r = subprocess.run([DOVOD, 'check', path], capture_output=True, text=True, timeout=300)
    return (r.stdout + r.stderr).strip()


def hidden(task, sol_dir):
    lines = ['use "solution.dv"', '']
    for i, expr in enumerate(task['hidden']):
        lines += [f'fn hidden_{i}() -> Bool', f'  ex hidden_{i}()', '{', f'  return {expr}', '}', '']
    p = os.path.join(sol_dir, 'hidden.dv')
    open(p, 'w').write('\n'.join(lines))
    rep, _ = check(p)
    fails = [e for e in rep['errors']]
    return len(task['hidden']) - len([e for e in fails if e.get('file', '').endswith('hidden.dv')]), fails


def run_task(task, out_dir):
    d = os.path.join(out_dir, task['id'])
    os.makedirs(d, exist_ok=True)
    msgs = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': task['prompt']}]
    res = {'id': task['id'], 'passed_check_at': None, 'own_example_failures': 0, 'error_codes': [],
           'hidden': None, 'assume': 0, 'unreached': 0, 'examples': 0}
    for n in range(1, ATTEMPTS + 1):
        t0 = time.time()
        reply = ask(msgs)
        code = extract(reply)
        sol = os.path.join(d, 'solution.dv')
        open(sol, 'w').write(code + '\n')
        open(os.path.join(d, f'attempt_{n}.dv'), 'w').write(code + '\n')
        rep, _ = check(sol)
        codes = [e['code'] for e in rep['errors']]
        res['error_codes'].append(codes)
        res['own_example_failures'] += codes.count('E-EXAMPLE')
        print(f"  {task['id']} attempt {n} ({time.time() - t0:.0f}s): "
              f"{'OK' if rep['ok'] else ', '.join(sorted(set(codes)))}", flush=True)
        if rep['ok']:
            res['passed_check_at'] = n
            res['assume'] = sum(1 for f in rep['functions'] if f['status'] == 'assumed')
            res['unreached'] = len(rep['warnings'])
            res['examples'] = sum(f['examples'] for f in rep['functions'])
            ok, fails = hidden(task, d)
            res['hidden'] = f"{ok}/{len(task['hidden'])}"
            res['hidden_fail'] = [f.get('msg', '') for f in fails][:5]
            print(f"  {task['id']} hidden tests: {res['hidden']}", flush=True)
            break
        msgs += [{'role': 'assistant', 'content': reply},
                 {'role': 'user', 'content': "`dovod check` says:\n\n" + human(sol)
                  + "\n\nFix the program. Reply with the full corrected file in one code block."}]
    json.dump(res, open(os.path.join(d, 'result.json'), 'w'), indent=1)
    return res


def main():
    only = sys.argv[1:]
    out_dir = os.path.join(HERE, 'runs', time.strftime('%Y%m%d-%H%M%S') + '-' + MODEL.replace(':', '_'))
    os.makedirs(out_dir)
    print(f"model {MODEL}, {ATTEMPTS} attempts, out {out_dir}", flush=True)
    results = [run_task(t, out_dir) for t in TASKS if not only or t['id'] in only]
    json.dump(results, open(os.path.join(out_dir, 'summary.json'), 'w'), indent=1)
    print("\nSUMMARY")
    print(f"{'task':16} {'check@':7} {'hidden':8} {'own-ex caught':14} {'examples':9} {'assume':7} unreached")
    for r in results:
        print(f"{r['id']:16} {str(r['passed_check_at'] or 'never'):7} {str(r['hidden'] or '-'):8} "
              f"{r['own_example_failures']:<14} {r['examples']:<9} {r['assume']:<7} {r['unreached']}")


if __name__ == '__main__':
    main()
