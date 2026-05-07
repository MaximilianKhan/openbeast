package main

import (
	"bufio"
	"fmt"
	"math"
	"math/cmplx"
	"os"
	"strconv"
)

func fft(x []complex128) []complex128 {
	n := len(x)
	if n == 1 {
		return []complex128{x[0]}
	}
	if n&(n-1) != 0 {
		panic("not power of 2")
	}
	e := make([]complex128, n/2)
	o := make([]complex128, n/2)
	for k := 0; k < n/2; k++ {
		e[k] = x[2*k]
		o[k] = x[2*k+1]
	}
	E := fft(e)
	O := fft(o)
	out := make([]complex128, n)
	for k := 0; k < n/2; k++ {
		t := cmplx.Exp(complex(0, -2*math.Pi*float64(k)/float64(n))) * O[k]
		out[k] = E[k] + t
		out[k+n/2] = E[k] - t
	}
	return out
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
		N, _ := strconv.Atoi(read())
		x := make([]complex128, N)
		for i := 0; i < N; i++ {
			v, _ := strconv.ParseFloat(read(), 64)
			x[i] = complex(v, 0)
		}
		f := fft(x)
		for _, v := range f {
			fmt.Fprintf(out, "%.4f %.4f\n", real(v), imag(v))
		}
	}
}
