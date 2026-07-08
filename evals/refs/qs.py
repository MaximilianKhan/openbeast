import sys


def three_way_qs(a, lo, hi):
    if lo >= hi:
        return
    pivot = a[lo + (hi - lo) // 2]
    lt, i, gt = lo, lo, hi
    while i <= gt:
        if a[i] < pivot:
            a[lt], a[i] = a[i], a[lt]
            lt += 1
            i += 1
        elif a[i] > pivot:
            a[gt], a[i] = a[i], a[gt]
            gt -= 1
        else:
            i += 1
    three_way_qs(a, lo, lt - 1)
    three_way_qs(a, gt + 1, hi)


def main():
    data = sys.stdin.read().split()
    idx = 0
    t = int(data[idx]); idx += 1
    out = []
    for _ in range(t):
        n = int(data[idx]); idx += 1
        arr = [int(x) for x in data[idx:idx + n]]
        idx += n
        if n > 0:
            three_way_qs(arr, 0, n - 1)
        out.append(" ".join(str(x) for x in arr))
    sys.stdout.write("\n".join(out) + "\n")


main()
