# README #

This example demonstrates how to set up and simulate a two-chamber (left and right ventricular) solid mechanics heart model coupled to a closed-loop
0D circulatory system. A full dynamic heart cycle of duration 1 s is simulated, where the active contraction is modeled by a prescribed active stress approach.
Passive material behavior of the heart muscle is governed by the Holzapfel-Ogden anisotropic strain energy function and a strain rate-dependent viscous
model.
We start the simulation with "prestressing" using the MULF method (Gee et al. 2010, Schein and Gee 2021), which allows to imprint loads without changing the geometry,
where the solid is loaded to the initial left and right ventricular pressures.
Thereafter, we kickstart the dynamic simulation with passive ventricular filling by the systole of the atria (0D chamber models). Ventricular systole
happens in t \in [0.2 s, 0.53 s], hence lasting a third of the whole cycle time. After systole, the heart relaxes and eventually fills to about the same pressure
as it has been initialized to.

NOTE: For demonstrative purposes, a fairly coarse finite element discretization is chosen here, which by no means yields a spatially converged solution and which
may be prone to locking phenomena. The user may increse the parameter 'order_disp' in the FEM_PARAMS section from 1 to 2 (and increase 'quad_degree' to 6)
such that quadratic finite element ansatz functions (instead of linear ones) are used. While this will increase accuracy and mitigate locking, computation time will
increase.

![Simulation setup.](https://github.com/marchirschvogel/ambit/assets/52761273/3ecc5dca-8855-4888-bf30-76d75e216e1a) \
**Simulation setup.**

### Instructions ###

Study the setup and the comments in the input file `solid_flow0d_heart_cycle.py`. Run the simulation, either in one of the provided Docker containers or using your own FEniCSx/Ambit installation, using the command
```
mpiexec -n 1 python3 solid_flow0d_heart_cycle.py
```
It is fully sufficient to use one core (mpiexec -n 1) for the presented setup, while you might want to use more (e.g., mpiexec -n 4) if you increase 'order_disp' to 2.

Open the results file results_solid_flow0d_heart_cycle_displacement.xdmf in Paraview, and visualize the deformation over the heart cycle.

For postprocessing of the time courses of pressures, volumes, and fluxes of the 0D model, either use your own tools to plot the text output files (first column is time, second is the respective
quantity), or make sure to have Gnuplot (and TeX) installed and navigate to the output folder (tmp/) in order to execute the script flow0d_plot.py (which lies in ambit/src/ambit_fe/postprocess/):
```
flow0d_plot.py -s solid_flow0d_heart_cycle -V0 117e3 93e3 0 0 0
```
A folder 'plot_solid_flow0d_heart_cycle' is created inside tmp/. Look at the results of pressures (p), volumes (V), and fluxes (q,Q) over time.
Subscripts v, at, ar, ven refer to 'ventricular', 'atrial', 'arterial', and 'venous', respectively. Superscripts l, r, sys, pul refer to 'left', 'right', 'systemic', and
'pulmonary', respectively.
Try to understand the time courses of the respective pressures, as well as the plots of ventricular pressure over volume.
Check that the overall system volume is constant and around 4-5 liters.

NOTE: This setup computes only one cardiac cycle, which does not yield a periodic state solution (compare e.g. initial and end-cyclic right ventricular pressures and volumes,
which do not coincide). Change the parameter number_of_cycles from 1 to 10 and re-run the simulation. The simulation will stop when the cycle error (relative change in 
0D variable quantities from beginning to end of a cycle) falls below the value of 'eps_periodic' (set to 5 %). How many cycles are needed to reach periodicity?

### High-fidelity solution

The figure and animation show a high-fidelity solution using a refined mesh and quadratic tetrahedral elements. Compare your solution from the coarser mesh. What is the deviation
in ventricular volume?

![A. Left heart and systemic pressures over time. B. Left and right ventricular and atrial volumes over time. C. Left and right ventricular pressure-volume relationships. D. Snapshot of heart deformation at end-systole, color indicates displacement magnitude.](https://github.com/marchirschvogel/ambit/assets/52761273/75bd35d4-7203-4320-a375-b4dc28e3d526) \
**A. Left heart and systemic pressures over time. B. Left and right ventricular and atrial volumes over time. C. Left and right ventricular pressure-volume relationships. D. Snapshot of heart deformation at end-systole, color indicates displacement magnitude.**

https://github.com/marchirschvogel/ambit/assets/52761273/61336888-82e1-4484-97ea-b77d3fb4ce2f