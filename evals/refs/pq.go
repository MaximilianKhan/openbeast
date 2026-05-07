package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Heap struct{ h []int64 }

func (H *Heap) push(v int64) {
	H.h = append(H.h, v)
	i := len(H.h) - 1
	for i > 0 {
		p := (i - 1) / 2
		if H.h[p] > H.h[i] {
			H.h[p], H.h[i] = H.h[i], H.h[p]
			i = p
		} else {
			break
		}
	}
}
func (H *Heap) pop() (int64, bool) {
	if len(H.h) == 0 {
		return 0, false
	}
	top := H.h[0]
	last := H.h[len(H.h)-1]
	H.h = H.h[:len(H.h)-1]
	if len(H.h) > 0 {
		H.h[0] = last
		i := 0
		n := len(H.h)
		for {
			l := 2*i + 1
			r := 2*i + 2
			m := i
			if l < n && H.h[l] < H.h[m] {
				m = l
			}
			if r < n && H.h[r] < H.h[m] {
				m = r
			}
			if m != i {
				H.h[m], H.h[i] = H.h[i], H.h[m]
				i = m
			} else {
				break
			}
		}
	}
	return top, true
}

func main() {
	in := bufio.NewReader(os.Stdin)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	readLine := func() string {
		line, _ := in.ReadString('\n')
		return strings.TrimRight(line, "\n")
	}
	T, _ := strconv.Atoi(readLine())
	for c := 0; c < T; c++ {
		Q, _ := strconv.Atoi(readLine())
		H := &Heap{}
		for i := 0; i < Q; i++ {
			op := readLine()
			if strings.HasPrefix(op, "p ") {
				v, _ := strconv.ParseInt(op[2:], 10, 64)
				H.push(v)
			} else {
				v, ok := H.pop()
				if ok {
					fmt.Fprintln(out, v)
				} else {
					fmt.Fprintln(out, "-")
				}
			}
		}
	}
}
