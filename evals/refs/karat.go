package main

import (
	"bufio"
	"os"
	"strconv"
)

// Karatsuba multiplication on little-endian base-256 byte arrays.
// Pure []byte digit ops (no math/big, no encoding/binary).

const cutoff = 32

// trim removes leading (most-significant) zero digits, keeping at least one.
func trim(a []byte) []byte {
	n := len(a)
	for n > 1 && a[n-1] == 0 {
		n--
	}
	return a[:n]
}

// add returns a+b as a new little-endian byte slice.
func add(a, b []byte) []byte {
	n := len(a)
	if len(b) > n {
		n = len(b)
	}
	out := make([]byte, n+1)
	carry := 0
	for i := 0; i < n; i++ {
		s := carry
		if i < len(a) {
			s += int(a[i])
		}
		if i < len(b) {
			s += int(b[i])
		}
		out[i] = byte(s & 0xFF)
		carry = s >> 8
	}
	out[n] = byte(carry)
	return trim(out)
}

// sub returns a-b assuming a >= b, trimmed.
func sub(a, b []byte) []byte {
	out := make([]byte, len(a))
	borrow := 0
	for i := 0; i < len(a); i++ {
		d := int(a[i]) - borrow
		if i < len(b) {
			d -= int(b[i])
		}
		if d < 0 {
			d += 256
			borrow = 1
		} else {
			borrow = 0
		}
		out[i] = byte(d)
	}
	return trim(out)
}

// school does O(n*m) schoolbook multiplication.
func school(a, b []byte) []byte {
	out := make([]int, len(a)+len(b))
	for i := 0; i < len(a); i++ {
		x := int(a[i])
		if x == 0 {
			continue
		}
		carry := 0
		for j := 0; j < len(b); j++ {
			s := out[i+j] + x*int(b[j]) + carry
			out[i+j] = s & 0xFF
			carry = s >> 8
		}
		k := i + len(b)
		for carry != 0 {
			s := out[k] + carry
			out[k] = s & 0xFF
			carry = s >> 8
			k++
		}
	}
	res := make([]byte, len(out))
	for i, v := range out {
		res[i] = byte(v)
	}
	return trim(res)
}

func karat(a, b []byte) []byte {
	if len(a) <= cutoff || len(b) <= cutoff {
		return school(a, b)
	}
	m := len(a)
	if len(b) < m {
		m = len(b)
	}
	m /= 2
	a0, a1 := a[:m], a[m:]
	b0, b1 := b[:m], b[m:]
	z0 := karat(a0, b0)
	z2 := karat(a1, b1)
	z1 := sub(sub(karat(add(a0, a1), add(b0, b1)), z0), z2)

	out := make([]int, len(a)+len(b)+4)
	for i, v := range z0 {
		out[i] += int(v)
	}
	for i, v := range z1 {
		out[i+m] += int(v)
	}
	for i, v := range z2 {
		out[i+2*m] += int(v)
	}
	carry := 0
	for i := range out {
		s := out[i] + carry
		out[i] = s & 0xFF
		carry = s >> 8
	}
	res := make([]byte, len(out))
	for i, v := range out {
		res[i] = byte(v)
	}
	return trim(res)
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
		p := karat(trim(a), trim(b))
		for i, v := range p {
			if i > 0 {
				w.WriteByte(' ')
			}
			w.WriteString(strconv.Itoa(int(v)))
		}
		w.WriteByte('\n')
	}
}
