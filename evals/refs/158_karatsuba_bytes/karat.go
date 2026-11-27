package main

import (
	"bufio"
	"os"
	"strconv"
)

// Karatsuba multiplication on little-endian base-256 byte arrays.
// The recursion multiplies the digit sequences as polynomials with int64
// coefficients (each one a small sum of digit products, never a collapsed
// big number); a single carry pass turns the product back into bytes.
// No math/big, no encoding/binary.

const cutoff = 32

// school adds a*b into r (len(r) >= len(a)+len(b)-1).
func school(a, b, r []int64) {
	for i, x := range a {
		if x == 0 {
			continue
		}
		row := r[i : i+len(b)]
		for j, y := range b {
			row[j] += x * y
		}
	}
}

// poly adds a*b into r, len(a) == len(b) == n, len(r) >= 2n-1.
func poly(a, b, r []int64) {
	n := len(a)
	if n <= cutoff {
		school(a, b, r)
		return
	}
	h := (n + 1) / 2
	a1 := make([]int64, h)
	b1 := make([]int64, h)
	copy(a1, a[h:])
	copy(b1, b[h:])
	sa := make([]int64, h)
	sb := make([]int64, h)
	for i := 0; i < h; i++ {
		sa[i] = a[i] + a1[i]
		sb[i] = b[i] + b1[i]
	}
	z0 := make([]int64, 2*h-1)
	z2 := make([]int64, 2*h-1)
	zm := make([]int64, 2*h-1)
	poly(a[:h], b[:h], z0)
	poly(a1, b1, z2)
	poly(sa, sb, zm)
	rl := 2*n - 1
	for i := 0; i < 2*h-1; i++ {
		r[i] += z0[i]
		r[i+h] += zm[i] - z0[i] - z2[i]
		if i+2*h < rl {
			r[i+2*h] += z2[i]
		}
	}
}

func karat(a, b []byte) []byte {
	n := len(a)
	if len(b) > n {
		n = len(b)
	}
	pa := make([]int64, n)
	pb := make([]int64, n)
	for i, v := range a {
		pa[i] = int64(v)
	}
	for i, v := range b {
		pb[i] = int64(v)
	}
	c := make([]int64, 2*n-1)
	poly(pa, pb, c)
	out := make([]byte, 0, 2*n+8)
	var carry int64
	for _, v := range c {
		s := v + carry
		out = append(out, byte(s&0xFF))
		carry = s >> 8
	}
	for carry != 0 {
		out = append(out, byte(carry&0xFF))
		carry >>= 8
	}
	for len(out) > 1 && out[len(out)-1] == 0 {
		out = out[:len(out)-1]
	}
	return out
}

func main() {
	rd := bufio.NewReaderSize(os.Stdin, 1<<20)
	w := bufio.NewWriterSize(os.Stdout, 1<<20)
	defer w.Flush()

	readInt := func() int {
		n := 0
		c, err := rd.ReadByte()
		for err == nil && (c == ' ' || c == '\n' || c == '\r' || c == '\t') {
			c, err = rd.ReadByte()
		}
		for err == nil && c >= '0' && c <= '9' {
			n = n*10 + int(c-'0')
			c, err = rd.ReadByte()
		}
		return n
	}

	t := readInt()
	for c := 0; c < t; c++ {
		na := readInt()
		nb := readInt()
		a := make([]byte, na)
		b := make([]byte, nb)
		for i := 0; i < na; i++ {
			a[i] = byte(readInt())
		}
		for i := 0; i < nb; i++ {
			b[i] = byte(readInt())
		}
		p := karat(a, b)
		for i, v := range p {
			if i > 0 {
				w.WriteByte(' ')
			}
			w.WriteString(strconv.Itoa(int(v)))
		}
		w.WriteByte('\n')
	}
}
