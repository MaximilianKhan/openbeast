// WRONG (163_b): 2nd-edition D3 rule; q, r right, D3 counter wrong.
package main

import (
	"bufio"
	"io"
	"os"
	"strconv"
)

func trim(a []uint32) []uint32 {
	n := len(a)
	for n > 1 && a[n-1] == 0 {
		n--
	}
	return a[:n]
}

func nlz(x uint32) uint {
	s := uint(0)
	for x&0x80000000 == 0 {
		x <<= 1
		s++
	}
	return s
}

// divmodD returns q, r and the D3 / D6 counters.
func divmodD(u, v []uint32) ([]uint32, []uint32, int, int) {
	lu := len(u)
	for lu > 0 && u[lu-1] == 0 {
		lu--
	}
	u = u[:lu]
	v = trim(v)
	n := len(v)
	if lu < n {
		if lu == 0 {
			return []uint32{0}, []uint32{0}, 0, 0
		}
		return []uint32{0}, u, 0, 0
	}
	if n == 1 {
		q := make([]uint32, lu)
		var rem uint64
		d := uint64(v[0])
		for i := lu - 1; i >= 0; i-- {
			cur := rem<<32 | uint64(u[i])
			q[i] = uint32(cur / d)
			rem = cur % d
		}
		return trim(q), []uint32{uint32(rem)}, 0, 0
	}
	m := lu - n
	s := nlz(v[n-1])
	vn := make([]uint32, n)
	un := make([]uint32, lu+1)
	for i := n - 1; i > 0; i-- {
		vn[i] = v[i]<<s | uint32(uint64(v[i-1])>>(32-s))
	}
	vn[0] = v[0] << s
	un[lu] = uint32(uint64(u[lu-1]) >> (32 - s))
	for i := lu - 1; i > 0; i-- {
		un[i] = u[i]<<s | uint32(uint64(u[i-1])>>(32-s))
	}
	un[0] = u[0] << s
	q := make([]uint32, m+1)
	const B = uint64(1) << 32
	d3, d6 := 0, 0
	v1, v2 := uint64(vn[n-1]), uint64(vn[n-2])
	for j := m; j >= 0; j-- {
		num := uint64(un[j+n])<<32 | uint64(un[j+n-1])
		qhat := num / v1
		if uint64(un[j+n]) == v1 {
			qhat = B - 1
		}
		rhat := num - qhat*v1
		for rhat < B && qhat*v2 > (rhat<<32|uint64(un[j+n-2])) {
			qhat--
			d3++
			rhat += v1
			if rhat >= B {
				break
			}
		}
		var borrow, carry uint64
		for i := 0; i < n; i++ {
			p := qhat*uint64(vn[i]) + carry
			carry = p >> 32
			t := uint64(un[i+j]) - (p & 0xFFFFFFFF) - borrow
			un[i+j] = uint32(t)
			borrow = (t >> 32) & 1
		}
		t := uint64(un[j+n]) - carry - borrow
		un[j+n] = uint32(t)
		if t>>63 != 0 {
			d6++
			qhat--
			var c uint64
			for i := 0; i < n; i++ {
				sum := uint64(un[i+j]) + uint64(vn[i]) + c
				un[i+j] = uint32(sum)
				c = sum >> 32
			}
			un[j+n] += uint32(c)
		}
		q[j] = uint32(qhat)
	}
	r := make([]uint32, n)
	for i := 0; i < n-1; i++ {
		r[i] = un[i]>>s | uint32(uint64(un[i+1])<<(32-s))
	}
	r[n-1] = un[n-1] >> s
	return trim(q), trim(r), d3, d6
}

func main() {
	data, _ := io.ReadAll(bufio.NewReaderSize(os.Stdin, 1<<20))
	p := 0
	next := func() uint64 {
		for p < len(data) && (data[p] < '0' || data[p] > '9') {
			p++
		}
		var x uint64
		for p < len(data) && data[p] >= '0' && data[p] <= '9' {
			x = x*10 + uint64(data[p]-'0')
			p++
		}
		return x
	}
	w := bufio.NewWriterSize(os.Stdout, 1<<20)
	defer w.Flush()
	put := func(a []uint32) {
		for i, x := range a {
			if i > 0 {
				w.WriteByte(' ')
			}
			w.WriteString(strconv.FormatUint(uint64(x), 10))
		}
		w.WriteByte('\n')
	}
	t := int(next())
	for ; t > 0; t-- {
		lu, lv := int(next()), int(next())
		u := make([]uint32, lu)
		v := make([]uint32, lv)
		for i := range u {
			u[i] = uint32(next())
		}
		for i := range v {
			v[i] = uint32(next())
		}
		q, r, d3, d6 := divmodD(u, v)
		put(q)
		put(r)
		w.WriteString(strconv.Itoa(d3) + " " + strconv.Itoa(d6) + "\n")
	}
}
