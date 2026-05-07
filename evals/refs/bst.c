#include <stdio.h>
#include <stdlib.h>

typedef struct Node {
    int v;
    struct Node *l, *r;
} Node;

static Node* new_node(int v) {
    Node *n = (Node*)malloc(sizeof(Node));
    n->v = v; n->l = n->r = NULL;
    return n;
}

static Node* insert(Node *root, int v) {
    if (!root) return new_node(v);
    Node *cur = root;
    for (;;) {
        if (v < cur->v) {
            if (!cur->l) { cur->l = new_node(v); return root; }
            cur = cur->l;
        } else if (v > cur->v) {
            if (!cur->r) { cur->r = new_node(v); return root; }
            cur = cur->r;
        } else return root;
    }
}

static void free_tree(Node *root) {
    if (!root) return;
    free_tree(root->l);
    free_tree(root->r);
    free(root);
}

static void inorder(Node *root, int *out, int *idx) {
    Node *stack[100000];
    int top = 0;
    Node *cur = root;
    while (cur || top > 0) {
        while (cur) { stack[top++] = cur; cur = cur->l; }
        cur = stack[--top];
        out[(*idx)++] = cur->v;
        cur = cur->r;
    }
}

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    while (T--) {
        int N;
        if (scanf("%d", &N) != 1) return 1;
        Node *root = NULL;
        for (int i = 0; i < N; i++) {
            int v;
            if (scanf("%d", &v) != 1) return 1;
            root = insert(root, v);
        }
        int out[100000]; int idx = 0;
        inorder(root, out, &idx);
        for (int i = 0; i < idx; i++) {
            if (i > 0) putchar(' ');
            printf("%d", out[i]);
        }
        putchar('\n');
        free_tree(root);
    }
    return 0;
}
