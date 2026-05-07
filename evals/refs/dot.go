package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
)

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
		a := make([]int64, N)
		b := make([]int64, N)
		for i := 0; i < N; i++ {
			v, _ := strconv.ParseInt(read(), 10, 64)
			a[i] = v
		}
		for i := 0; i < N; i++ {
			v, _ := strconv.ParseInt(read(), 10, 64)
			b[i] = v
		}
		var s int64 = 0
		for i := 0; i < N; i++ {
			s += a[i] * b[i]
		}
		fmt.Fprintln(out, s)
	}
}
