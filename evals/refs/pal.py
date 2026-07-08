import sys

out = []
for line in sys.stdin:
    line = line.rstrip('\n')
    s = [c.lower() for c in line if c.isalnum()]
    out.append('true' if s == s[::-1] else 'false')
sys.stdout.write('\n'.join(out) + ('\n' if out else ''))
