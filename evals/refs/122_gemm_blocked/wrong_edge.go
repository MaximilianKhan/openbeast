package main

import (
	"bufio"
	"fmt"
	"os"
)

const bs = 64

func main() {
	in := bufio.NewReader(os.Stdin)
	var n int
	var seed uint64
	if _, err := fmt.Fscan(in, &n, &seed); err != nil || n < 1 {
		os.Exit(1)
	}
	nn := n * n
	A := make([]float64, nn)
	B := make([]float64, nn)
	C := make([]float64, nn)

	const P, G uint64 = 2147483647, 48271
	x := seed
	for t := 0; t < nn; t++ {
		x = x * G % P
		A[t] = float64(x) / float64(P)
	}
	for t := 0; t < nn; t++ {
		x = x * G % P
		B[t] = float64(x) / float64(P)
	}

	for ii := 0; ii < n; ii += bs {
		ie := min(ii+bs, n)
		for kk := 0; kk < n; kk += bs {
			ke := min(kk+bs, n)
			for jj := 0; jj < n; jj += bs {
				je := min(jj+bs, n-1)
				for i := ii; i < ie; i++ {
					crow := C[i*n+jj : i*n+je]
					for k := kk; k < ke; k++ {
						aik := A[i*n+k]
						brow := B[k*n+jj : k*n+je]
						for j := range crow {
							crow[j] += aik * brow[j]
						}
					}
				}
			}
		}
	}

	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	for i := 0; i < n; i++ {
		row := C[i*n : (i+1)*n]
		s, m := 0.0, row[0]
		for _, c := range row {
			s += c * c
			if c > m {
				m = c
			}
		}
		fmt.Fprintf(out, "%.12e %.12e\n", s, m)
	}
}
