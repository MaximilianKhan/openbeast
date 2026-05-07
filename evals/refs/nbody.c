#include <stdio.h>
#include <stdlib.h>
#include <math.h>

static void accel(double **p, double *mass, int n, double **a) {
    for (int i = 0; i < n; i++) { a[i][0] = a[i][1] = 0.0; }
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            if (i == j) continue;
            double dx = p[j][0] - p[i][0];
            double dy = p[j][1] - p[i][1];
            double r2 = dx*dx + dy*dy;
            double r = sqrt(r2);
            if (r > 1e-12) {
                double f = mass[j] / (r2 * r);
                a[i][0] += f*dx; a[i][1] += f*dy;
            }
        }
    }
}

int main(void) {
    int T; scanf("%d", &T);
    while (T--) {
        int N, steps; double dt;
        scanf("%d %lf %d", &N, &dt, &steps);
        double **pos = (double**)malloc((size_t)N*sizeof(double*));
        double **vel = (double**)malloc((size_t)N*sizeof(double*));
        double **a   = (double**)malloc((size_t)N*sizeof(double*));
        double **a2  = (double**)malloc((size_t)N*sizeof(double*));
        double *mass = (double*)malloc((size_t)N*sizeof(double));
        for (int i = 0; i < N; i++) {
            pos[i] = (double*)malloc(2*sizeof(double));
            vel[i] = (double*)malloc(2*sizeof(double));
            a[i]   = (double*)malloc(2*sizeof(double));
            a2[i]  = (double*)malloc(2*sizeof(double));
            scanf("%lf %lf %lf %lf %lf", &mass[i], &pos[i][0], &pos[i][1], &vel[i][0], &vel[i][1]);
        }
        accel(pos, mass, N, a);
        for (int s = 0; s < steps; s++) {
            for (int i = 0; i < N; i++) {
                pos[i][0] += vel[i][0]*dt + 0.5*a[i][0]*dt*dt;
                pos[i][1] += vel[i][1]*dt + 0.5*a[i][1]*dt*dt;
            }
            accel(pos, mass, N, a2);
            for (int i = 0; i < N; i++) {
                vel[i][0] += 0.5*(a[i][0] + a2[i][0])*dt;
                vel[i][1] += 0.5*(a[i][1] + a2[i][1])*dt;
                a[i][0] = a2[i][0]; a[i][1] = a2[i][1];
            }
        }
        for (int i = 0; i < N; i++) {
            printf("%.6f %.6f %.6f %.6f\n", pos[i][0], pos[i][1], vel[i][0], vel[i][1]);
        }
        for (int i = 0; i < N; i++) { free(pos[i]); free(vel[i]); free(a[i]); free(a2[i]); }
        free(pos); free(vel); free(a); free(a2); free(mass);
    }
    return 0;
}
