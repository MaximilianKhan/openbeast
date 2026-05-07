package main

import (
	"bufio"
	"fmt"
	"math/big"
	"os"
	"strconv"
)

func gcdU(a, b uint64) uint64 {
	for b != 0 {
		a, b = b, a%b
	}
	return a
}

func mulmod(a, b, m uint64) uint64 {
	A := new(big.Int).SetUint64(a)
	B := new(big.Int).SetUint64(b)
	M := new(big.Int).SetUint64(m)
	A.Mul(A, B)
	A.Mod(A, M)
	return A.Uint64()
}

func pollard(n uint64) uint64 {
	if n%2 == 0 {
		return 2
	}
	var c uint64 = 1
	for {
		var x, y uint64 = 2, 2
		var d uint64 = 1
		for d == 1 {
			x = (mulmod(x, x, n) + c) % n
			y = (mulmod(y, y, n) + c) % n
			y = (mulmod(y, y, n) + c) % n
			var diff uint64
			if x >= y {
				diff = x - y
			} else {
				diff = y - x
			}
			d = gcdU(diff, n)
		}
		if d != n {
			return d
		}
		c++
	}
}

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 1<<20)
	in.Split(bufio.ScanWords)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	read := func() string { in.Scan(); return in.Text() }
	T, _ := strconv.Atoi(read())
	for k := 0; k < T; k++ {
		n, _ := strconv.ParseUint(read(), 10, 64)
		fmt.Fprintln(out, pollard(n))
	}
}
