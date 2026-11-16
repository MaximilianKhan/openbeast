// Correct but too slow: Karatsuba convolution mod p = 998244353 instead of
// an NTT.
package main

import (
	"bufio"
	"os"
	"strconv"
)

const MOD uint64 = 998244353
const cut = 20000

// kara adds a*b into r (len 2n-1) for a, b of length n.
func kara(a, b []uint64, r []uint64) {
	n := len(a)
	if n <= cut {
		for i := 0; i < n; i++ {
			x := a[i]
			if x == 0 {
				continue
			}
			for j := 0; j < n; j++ {
				r[i+j] = (r[i+j] + x*b[j]) % MOD
			}
		}
		return
	}
	h := (n + 1) / 2
	a1 := make([]uint64, h)
	b1 := make([]uint64, h)
	copy(a1, a[h:])
	copy(b1, b[h:])
	sa := make([]uint64, h)
	sb := make([]uint64, h)
	for i := 0; i < h; i++ {
		sa[i] = (a[i] + a1[i]) % MOD
		sb[i] = (b[i] + b1[i]) % MOD
	}
	z0 := make([]uint64, 2*h-1)
	z2 := make([]uint64, 2*h-1)
	zm := make([]uint64, 2*h-1)
	kara(a[:h], b[:h], z0)
	kara(a1, b1, z2)
	kara(sa, sb, zm)
	rl := 2*n - 1
	for i := 0; i < 2*h-1; i++ {
		r[i] = (r[i] + z0[i]) % MOD
		mid := (zm[i] + 2*MOD - z0[i] - z2[i]) % MOD
		r[i+h] = (r[i+h] + mid) % MOD
		if i+2*h < rl {
			r[i+2*h] = (r[i+2*h] + z2[i]) % MOD
		}
	}
}

func main() {
	rd := bufio.NewReaderSize(os.Stdin, 1<<20)
	w := bufio.NewWriterSize(os.Stdout, 1<<20)
	defer w.Flush()
	readInt := func() uint64 {
		c, err := rd.ReadByte()
		for err == nil && (c < '0' || c > '9') {
			c, err = rd.ReadByte()
		}
		var x uint64
		for err == nil && c >= '0' && c <= '9' {
			x = x*10 + uint64(c-'0')
			c, err = rd.ReadByte()
		}
		return x
	}
	T := int(readInt())
	for ; T > 0; T-- {
		na := int(readInt())
		nb := int(readInt())
		n := na
		if nb > n {
			n = nb
		}
		a := make([]uint64, n)
		b := make([]uint64, n)
		for i := 0; i < na; i++ {
			a[i] = readInt() % MOD
		}
		for i := 0; i < nb; i++ {
			b[i] = readInt() % MOD
		}
		if na == 0 || nb == 0 {
			w.WriteString("0\n")
			continue
		}
		r := make([]uint64, 2*n-1)
		kara(a, b, r)
		for i := 0; i < na+nb-1; i++ {
			if i > 0 {
				w.WriteByte(' ')
			}
			w.WriteString(strconv.FormatUint(r[i], 10))
		}
		w.WriteByte('\n')
	}
}
