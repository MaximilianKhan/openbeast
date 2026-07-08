package main

import (
	"bufio"
	"fmt"
	"math/bits"
	"os"
)

// mulmod computes (a*b) % m using 128-bit intermediate via math/bits.
func mulmod(a, b, m uint64) uint64 {
	hi, lo := bits.Mul64(a, b)
	_, rem := bits.Div64(hi%m, lo, m)
	return rem
}

func powmod(base, exp, m uint64) uint64 {
	if m == 1 {
		return 0
	}
	result := uint64(1)
	base %= m
	for exp > 0 {
		if exp&1 == 1 {
			result = mulmod(result, base, m)
		}
		base = mulmod(base, base, m)
		exp >>= 1
	}
	return result
}

func tonelli(n, p uint64) (uint64, bool) {
	n %= p
	if n == 0 {
		return 0, true
	}
	if p == 2 {
		return n % 2, true
	}
	if powmod(n, (p-1)/2, p) != 1 {
		return 0, false
	}
	if p%4 == 3 {
		return powmod(n, (p+1)/4, p), true
	}

	q := p - 1
	s := uint64(0)
	for q%2 == 0 {
		q /= 2
		s++
	}

	z := uint64(2)
	for powmod(z, (p-1)/2, p) != p-1 {
		z++
	}

	m := s
	c := powmod(z, q, p)
	t := powmod(n, q, p)
	r := powmod(n, (q+1)/2, p)

	for t != 1 {
		i := uint64(0)
		t2 := t
		for i = 1; i < m; i++ {
			t2 = mulmod(t2, t2, p)
			if t2 == 1 {
				break
			}
		}
		b := c
		for k := uint64(0); k < m-i-1; k++ {
			b = mulmod(b, b, p)
		}
		m = i
		c = mulmod(b, b, p)
		t = mulmod(t, c, p)
		r = mulmod(r, b, p)
	}
	return r, true
}

func main() {
	reader := bufio.NewReader(os.Stdin)
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()

	var t int
	fmt.Fscan(reader, &t)
	for ; t > 0; t-- {
		var n, p uint64
		fmt.Fscan(reader, &n, &p)
		r, ok := tonelli(n, p)
		if ok {
			fmt.Fprintln(writer, r)
		} else {
			fmt.Fprintln(writer, "NONE")
		}
	}
}
