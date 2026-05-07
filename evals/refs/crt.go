package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
)

func egcd(a, b int64) (int64, int64, int64) {
	if b == 0 {
		return a, 1, 0
	}
	g, x1, y1 := egcd(b, a%b)
	return g, y1, x1 - (a/b)*y1
}

func modinv(a, n int64) (int64, bool) {
	a = ((a % n) + n) % n
	g, x, _ := egcd(a, n)
	if g != 1 {
		return 0, false
	}
	return ((x % n) + n) % n, true
}

func gcd(a, b int64) int64 {
	if a < 0 { a = -a }
	if b < 0 { b = -b }
	for b != 0 {
		a, b = b, a%b
	}
	return a
}

func crt(rs, ms []int64) int64 {
	var M int64 = 1
	var x int64 = 0
	for i := range rs {
		r, m := rs[i], ms[i]
		g := gcd(M, m)
		if (((r-x)%g)+g)%g != 0 {
			return -1
		}
		m2 := m / g
		M2 := M / g
		inv, ok := modinv(M2%m2, m2)
		if !ok {
			return -1
		}
		k := ((((r-x)/g)*inv)%m2 + m2) % m2
		x = x + M*k
		M = M * m2
		x = ((x % M) + M) % M
	}
	if M == 0 {
		return 0
	}
	return ((x % M) + M) % M
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
		K, _ := strconv.Atoi(read())
		rs := make([]int64, K)
		ms := make([]int64, K)
		for i := 0; i < K; i++ {
			r, _ := strconv.ParseInt(read(), 10, 64)
			m, _ := strconv.ParseInt(read(), 10, 64)
			rs[i] = r
			ms[i] = m
		}
		fmt.Fprintln(out, crt(rs, ms))
	}
}
