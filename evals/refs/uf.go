package main

import (
	"bufio"
	"os"
	"strconv"
)

var parent []int
var size []int

func find(x int) int {
	root := x
	for parent[root] != root {
		root = parent[root]
	}
	for parent[x] != root {
		parent[x], x = root, parent[x]
	}
	return root
}

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024*64)
	scanner.Split(bufio.ScanWords)
	var fields []string
	for scanner.Scan() {
		fields = append(fields, scanner.Text())
	}
	idx := 0
	nextStr := func() string { s := fields[idx]; idx++; return s }
	nextInt := func() int { v, _ := strconv.Atoi(fields[idx]); idx++; return v }

	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()

	n := nextInt()
	q := nextInt()
	parent = make([]int, n)
	size = make([]int, n)
	for i := 0; i < n; i++ {
		parent[i] = i
		size[i] = 1
	}
	for i := 0; i < q; i++ {
		op := nextStr()
		a := nextInt()
		b := nextInt()
		if op == "u" {
			ra, rb := find(a), find(b)
			if ra != rb {
				if size[ra] < size[rb] {
					ra, rb = rb, ra
				}
				parent[rb] = ra
				size[ra] += size[rb]
			}
		} else {
			if find(a) == find(b) {
				writer.WriteString("true\n")
			} else {
				writer.WriteString("false\n")
			}
		}
	}
}
