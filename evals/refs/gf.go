package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

func parseByte(s string) int {
	if strings.HasPrefix(s, "0x") || strings.HasPrefix(s, "0X") {
		v, _ := strconv.ParseInt(s[2:], 16, 64)
		return int(v)
	}
	v, _ := strconv.Atoi(s)
	return v
}

func gfmul(a, b int) int {
	p := 0
	for i := 0; i < 8; i++ {
		if b&1 != 0 {
			p ^= a
		}
		b >>= 1
		hi := a & 0x80
		a = (a << 1) & 0xFF
		if hi != 0 {
			a ^= 0x1B
		}
	}
	return p
}

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 1<<20)
	in.Split(bufio.ScanWords)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	read := func() string { in.Scan(); return in.Text() }
	T, _ := strconv.Atoi(read())
	for c := 0; c < T; c++ {
		op := read()
		a := parseByte(read())
		b := parseByte(read())
		if op == "+" {
			fmt.Fprintln(out, a^b)
		} else {
			fmt.Fprintln(out, gfmul(a, b))
		}
	}
}
