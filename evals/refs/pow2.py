import sys


def main():
    out = []
    for line in sys.stdin:
        line = line.strip()
        if line == "":
            continue
        n = int(line)
        out.append("true" if n > 0 and (n & (n - 1)) == 0 else "false")
    sys.stdout.write("\n".join(out) + ("\n" if out else ""))


if __name__ == "__main__":
    main()
