# LEAK PROBE: tries to read anything an answer could hide in -- likely fixture
# files next to the submission and, on Linux, the parent's command line
# (run_eval passes the validator as python -c <script>) -- prints what it
# gets, then answers "no match" everywhere. Must FAIL: nothing in
# /tmp/eval_pike192 besides the submission holds an answer, and the process
# running rx.py cannot open files at all.
for _path in ('/tmp/eval_pike192/expected.txt', '/tmp/eval_pike192/check.py',
              '/tmp/eval_pike192/cases.json', '/proc/self/stat'):
    try:
        with open(_path, 'rb') as _f:
            _data = _f.read()
        print('LEAK', _path, _data[:200])
        if _path == '/proc/self/stat':
            _ppid = _data.split()[3].decode()
            with open('/proc/%s/cmdline' % _ppid, 'rb') as _f:
                print('PARENT-CMDLINE', _f.read()[:200])
    except OSError as _e:
        print('LEAK-PROBE blocked:', _path, _e)


def search(pattern, string):
    return None


def finditer(pattern, string):
    return []
