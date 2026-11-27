// Polynomial convolution via NTT mod p = 998244353, primitive root g = 3.
// Iterative radix-2 with bit-reversal permutation. math/bits.Mul64 + Div64
// for a 128-bit-safe modular multiply. Matches the reference Python NTT.
package main

import (
	"bufio"
	"math/bits"
	"os"
	"strconv"
)

const MOD uint64 = 998244353

func mulmod(a, b uint64) uint64 {
	hi, lo := bits.Mul64(a, b)
	_, rem := bits.Div64(hi, lo, MOD)
	return rem
}

func powmod(a, e uint64) uint64 {
	r := uint64(1)
	a %= MOD
	for e > 0 {
		if e&1 == 1 {
			r = mulmod(r, a)
		}
		a = mulmod(a, a)
		e >>= 1
	}
	return r
}

func ntt(a []uint64, inv bool) {
	n := len(a)
	j := 0
	for i := 1; i < n; i++ {
		bit := n >> 1
		for j&bit != 0 {
			j ^= bit
			bit >>= 1
		}
		j ^= bit
		if i < j {
			a[i], a[j] = a[j], a[i]
		}
	}
	for length := 2; length <= n; length <<= 1 {
		w := powmod(3, (MOD-1)/uint64(length))
		if inv {
			w = powmod(w, MOD-2)
		}
		half := length >> 1
		for i := 0; i < n; i += length {
			wn := uint64(1)
			for k := 0; k < half; k++ {
				u := a[i+k]
				v := mulmod(a[i+k+half], wn)
				a[i+k] = (u + v) % MOD
				a[i+k+half] = (u + MOD - v) % MOD
				wn = mulmod(wn, w)
			}
		}
	}
	if inv {
		ninv := powmod(uint64(n), MOD-2)
		for i := 0; i < n; i++ {
			a[i] = mulmod(a[i], ninv)
		}
	}
}

func conv(a, b []uint64) []uint64 {
	if len(a) == 0 || len(b) == 0 {
		return []uint64{0}
	}
	rl := len(a) + len(b) - 1
	n := 1
	for n < rl {
		n <<= 1
	}
	fa := make([]uint64, n)
	fb := make([]uint64, n)
	copy(fa, a)
	copy(fb, b)
	ntt(fa, false)
	ntt(fb, false)
	for i := 0; i < n; i++ {
		fa[i] = mulmod(fa[i], fb[i])
	}
	ntt(fa, true)
	return fa[:rl]
}

func main() {
	r := bufio.NewReaderSize(os.Stdin, 1<<20)
	w := bufio.NewWriterSize(os.Stdout, 1<<20)
	defer w.Flush()

	readInt := func() (uint64, bool) {
		var c byte
		var err error
		for {
			c, err = r.ReadByte()
			if err != nil {
				return 0, false
			}
			if c >= '0' && c <= '9' {
				break
			}
		}
		var x uint64
		for c >= '0' && c <= '9' {
			x = x*10 + uint64(c-'0')
			c, err = r.ReadByte()
			if err != nil {
				break
			}
		}
		return x, true
	}

	t64, ok := readInt()
	if !ok {
		return
	}
	T := int(t64)
	for ; T > 0; T-- {
		na64, ok := readInt()
		if !ok {
			break
		}
		nb64, _ := readInt()
		na := int(na64)
		nb := int(nb64)
		a := make([]uint64, na)
		b := make([]uint64, nb)
		for i := 0; i < na; i++ {
			x, _ := readInt()
			a[i] = x % MOD
		}
		for i := 0; i < nb; i++ {
			x, _ := readInt()
			b[i] = x % MOD
		}
		c := conv(a, b)
		buf := make([]byte, 0, 12)
		for i, v := range c {
			if i > 0 {
				w.WriteByte(' ')
			}
			buf = strconv.AppendUint(buf[:0], v, 10)
			w.Write(buf)
		}
		w.WriteByte('\n')
	}
}
