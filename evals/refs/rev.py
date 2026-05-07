import sys
lines = sys.stdin.read().split('\n')
i = 0
T = int(lines[i]); i += 1
out = []
for _ in range(T):
    N = int(lines[i]); i += 1
    nums_line = lines[i]; i += 1
    if N == 0:
        out.append('')
        continue
    nums = nums_line.split()
    result = []
    k = len(nums) - 1
    while k >= 0:
        result.append(nums[k])
        k -= 1
    out.append(' '.join(result))
sys.stdout.write('\n'.join(out) + '\n')
