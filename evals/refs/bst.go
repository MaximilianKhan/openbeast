package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Node struct {
	v    int
	l, r *Node
}

func insert(root *Node, v int) *Node {
	if root == nil {
		return &Node{v: v}
	}
	cur := root
	for {
		if v < cur.v {
			if cur.l == nil { cur.l = &Node{v: v}; return root }
			cur = cur.l
		} else if v > cur.v {
			if cur.r == nil { cur.r = &Node{v: v}; return root }
			cur = cur.r
		} else {
			return root
		}
	}
}

func inorder(root *Node) []int {
	out := []int{}
	stack := []*Node{}
	cur := root
	for cur != nil || len(stack) > 0 {
		for cur != nil {
			stack = append(stack, cur)
			cur = cur.l
		}
		cur = stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		out = append(out, cur.v)
		cur = cur.r
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
		var root *Node = nil
		for i := 0; i < N; i++ {
			v, _ := strconv.Atoi(read())
			root = insert(root, v)
		}
		vs := inorder(root)
		parts := make([]string, len(vs))
		for k, v := range vs {
			parts[k] = strconv.Itoa(v)
		}
		fmt.Fprintln(out, strings.Join(parts, " "))
	}
}
