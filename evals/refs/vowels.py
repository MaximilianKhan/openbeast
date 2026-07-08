import sys

out = []
for line in sys.stdin:
    line = line.rstrip('\n')
    count = sum(1 for c in line if c.lower() in 'aeiou')
    out.append(str(count))
sys.stdout.write('\n'.join(out) + ('\n' if out else ''))
