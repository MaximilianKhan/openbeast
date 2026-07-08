package main

import (
	"bufio"
	"fmt"
	"os"
)

func mulmod(a, b, m uint64) uint64 {
	var res uint64 = 0
	a %= m
	for b > 0 {
		if b&1 == 1 {
			res = (res + a) % m
		}
		a = (a << 1) % m
		b >>= 1
	}
	return res
}

func powmod(base, exp, m uint64) uint64 {
	var res uint64 = 1
	base %= m
	for exp > 0 {
		if exp&1 == 1 {
			res = mulmod(res, base, m)
		}
		base = mulmod(base, base, m)
		exp >>= 1
	}
	return res
}

func isPrime(n uint64) bool {
	if n < 2 {
		return false
	}
	primes := []uint64{2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37}
	for _, p := range primes {
		if n == p {
			return true
		}
		if n%p == 0 {
			return false
		}
	}
	d := n - 1
	s := 0
	for d&1 == 0 {
		d >>= 1
		s++
	}
	for _, a := range primes {
		x := powmod(a, d, n)
		if x == 1 || x == n-1 {
			continue
		}
		composite := true
		for i := 0; i < s-1; i++ {
			x = mulmod(x, x, n)
			if x == n-1 {
				composite = false
				break
			}
		}
		if composite {
			return false
		}
	}
	return true
}

func main() {
	reader := bufio.NewReader(os.Stdin)
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()
	var T int
	fmt.Fscan(reader, &T)
	for i := 0; i < T; i++ {
		var n uint64
		fmt.Fscan(reader, &n)
		if isPrime(n) {
			fmt.Fprintln(writer, "true")
		} else {
			fmt.Fprintln(writer, "false")
		}
	}
}
