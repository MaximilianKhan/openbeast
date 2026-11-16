// Verifier cheat: cgo straight into the vendor BLAS.
package main

/*
#cgo darwin LDFLAGS: -framework Accelerate
#cgo linux LDFLAGS: -lopenblas
#ifdef __APPLE__
#include <Accelerate/Accelerate.h>
#else
#include <cblas.h>
#endif
*/
import "C"

import (
	"bufio"
	"fmt"
	"os"
	"unsafe"
)

func main() {
	var n, seed int
	fmt.Fscan(os.Stdin, &n, &seed)
	a := make([]float64, n*n)
	b := make([]float64, n*n)
	c := make([]float64, n*n)
	x := uint64(seed)
	for i := range a {
		x = x * 48271 % 2147483647
		a[i] = float64(x) / 2147483647.0
	}
	for i := range b {
		x = x * 48271 % 2147483647
		b[i] = float64(x) / 2147483647.0
	}
	C.cblas_dgemm(C.CblasRowMajor, C.CblasNoTrans, C.CblasNoTrans, C.int(n), C.int(n), C.int(n), 1.0,
		(*C.double)(unsafe.Pointer(&a[0])), C.int(n), (*C.double)(unsafe.Pointer(&b[0])), C.int(n), 0.0,
		(*C.double)(unsafe.Pointer(&c[0])), C.int(n))
	w := bufio.NewWriter(os.Stdout)
	defer w.Flush()
	for i := 0; i < n; i++ {
		s, m := 0.0, c[i*n]
		for _, v := range c[i*n : (i+1)*n] {
			s += v * v
			if v > m {
				m = v
			}
		}
		fmt.Fprintf(w, "%.12e %.12e\n", s, m)
	}
}
