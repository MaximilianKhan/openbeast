package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
)

func main() {
	reader := bufio.NewReaderSize(os.Stdin, 1<<20)
	writer := bufio.NewWriterSize(os.Stdout, 1<<20)
	defer writer.Flush()

	readFloat := func() float64 {
		// skip whitespace
		var b byte
		var err error
		for {
			b, err = reader.ReadByte()
			if err != nil {
				return 0
			}
			if b != ' ' && b != '\n' && b != '\r' && b != '\t' {
				break
			}
		}
		buf := []byte{b}
		for {
			b, err = reader.ReadByte()
			if err != nil {
				break
			}
			if b == ' ' || b == '\n' || b == '\r' || b == '\t' {
				break
			}
			buf = append(buf, b)
		}
		f, _ := strconv.ParseFloat(string(buf), 64)
		return f
	}

	n := int(readFloat())
	A := make([]float64, n*n)
	B := make([]float64, n*n)
	C := make([]float64, n*n)
	for i := range A {
		A[i] = readFloat()
	}
	for i := range B {
		B[i] = readFloat()
	}

	const BS = 32
	for ii := 0; ii < n; ii += BS {
		for jj := 0; jj < n; jj += BS {
			for kk := 0; kk < n; kk += BS {
				iEnd := ii + BS
				if iEnd > n {
					iEnd = n
				}
				jEnd := jj + BS
				if jEnd > n {
					jEnd = n
				}
				kEnd := kk + BS
				if kEnd > n {
					kEnd = n
				}
				for i := ii; i < iEnd; i++ {
					for k := kk; k < kEnd; k++ {
						aik := A[i*n+k]
						for j := jj; j < jEnd; j++ {
							C[i*n+j] += aik * B[k*n+j]
						}
					}
				}
			}
		}
	}

	for i := 0; i < n; i++ {
		for j := 0; j < n; j++ {
			if j > 0 {
				writer.WriteByte(' ')
			}
			fmt.Fprintf(writer, "%.6f", C[i*n+j])
		}
		writer.WriteByte('\n')
	}
}
