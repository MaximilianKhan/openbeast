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

	const MULT uint64 = 6364136223846793005
	const INC uint64 = 1442695040888963407
	const R2 uint64 = (1 << 30) * (1 << 30)

	T, _ := strconv.Atoi(read())
	for c := 0; c < T; c++ {
		N, _ := strconv.Atoi(read())
		seed, _ := strconv.ParseUint(read(), 10, 64)
		state := seed
		var count uint64 = 0
		for i := 0; i < N; i++ {
			state = state*MULT + INC
			x := (state >> 33) & 0x3FFFFFFF
			state = state*MULT + INC
			y := (state >> 33) & 0x3FFFFFFF
			if x*x+y*y < R2 {
				count++
			}
		}
		fmt.Fprintln(out, count)
	}
}
