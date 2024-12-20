#!/usr/bin/env python3

"""
FrSI test case of an axially clamped arterial segment
- mode partition of unity partitioning
- mode penalties
- ALE transient Stokes flow (Taylor-Hood elements)
- weak Dirichlet condition from fluid to ALE
"""

import ambit_fe

import sys
import numpy as np
from pathlib import Path
import pytest


@pytest.mark.frsi
@pytest.mark.fluid_ale
@pytest.mark.rom
def test_main():
    basepath = str(Path(__file__).parent.absolute())

    # reads in restart step from the command line
    try:
        restart_step = int(sys.argv[1])
    except:
        restart_step = 0

    IO_PARAMS = {
        "problem_type": "fluid_ale",
        "write_results_every": 1,
        "write_restart_every": 2,
        "restart_step": restart_step,
        "output_path": basepath + "/tmp/",
        "mesh_domain": basepath + "/input/artseg-quad_domain.xdmf",
        "mesh_boundary": basepath + "/input/artseg-quad_boundary.xdmf",
        "results_to_write": [["velocity", "pressure"], ["aledisplacement"]],  # first fluid, then ale results
        "simname": "frsi_artseg_modepartitionunity",
    }

    ROM_PARAMS = {
        "hdmfilenames": [basepath + "/input/artseg_vel_snapshot-*.txt"],
        "partitions": [
            basepath + "/input/artseg_part-1.txt",
            basepath + "/input/artseg_part-2.txt",
            basepath + "/input/artseg_part-3.txt",
        ],
        "numsnapshots": 1,
        "snapshotincr": 1,
        "numredbasisvec": 1,
        "eigenvalue_cutoff": 1.0e-8,
        "print_eigenproblem": True,
        "surface_rom": [1, 6],
        "regularizations": [
            0.0,
            0.0,
            10.0,
        ],  # add regularization to the mode at the junction in the middle (last one) ("dashpot-like")
        "regularizations_integ": [
            0.0,
            0.0,
            0.0,
        ],  # regularization on integral of reduced variable (here 0) ("spring-like")
        "regularizations_deriv": [0.0, 0.0, 0.0],
    }  # regularization on derivative of reduced variable (here 0) ("mass-like")

    SOLVER_PARAMS = {"solve_type": "direct", "tol_res": [1.0e-8, 1.0e-8, 1.0e-8], "tol_inc": [1.0e-8, 1.0e-8, 1.0e-8]}

    TIME_PARAMS = {
        "maxtime": 3.0,
        "numstep": 150,
        "numstep_stop": 3,
        "timint": "ost",
        "theta_ost": 0.5,
        "eval_nonlin_terms": "midpoint",
        "fluid_governing_type": "stokes_transient",
    }

    FEM_PARAMS_FLUID = {"order_vel": 2, "order_pres": 1, "quad_degree": 6, "fluid_formulation": "nonconservative"}

    FEM_PARAMS_ALE = {"order_disp": 2, "quad_degree": 6}

    COUPLING_PARAMS = {"coupling_fluid_ale": [{"surface_ids": [1, 6], "type": "strong_dirichlet"}]}

    MATERIALS_FLUID = {"MAT1": {"newtonian": {"mu": 4.0e-6}, "inertia": {"rho": 1.025e-6}}}

    MATERIALS_ALE = {"MAT1": {"linelast": {"Emod": 15.0, "nu": 0.1}}}

    # define your load curves here (syntax: tcX refers to curve X, to be used in BC_DICT key 'curve' : [X,0,0], or 'curve' : X)
    class time_curves:
        def tc1(self, t):
            t_ramp = 2.0
            p0 = 0.0
            pinfl = 1.0
            return (0.5 * (-(pinfl - p0)) * (1.0 - np.cos(np.pi * t / t_ramp)) + (-p0)) * (t < t_ramp) + (-pinfl) * (
                t >= t_ramp
            )

    BC_DICT_ALE = {
        "dirichlet": [
            {"id": [2, 3], "dir": "z", "val": 0.0},
            {"id": [4], "dir": "y", "val": 0.0},
            {"id": [5], "dir": "x", "val": 0.0},
        ]
    }

    BC_DICT_FLUID = {
        "membrane": [
            {
                "id": [1],
                "params": {"model": "membrane", "a_0": 1.0, "b_0": 6.0, "eta": 0.1, "rho0": 1e-6, "h0": {"val": 0.1}},
            },
            {
                "id": [6],
                "params": {"model": "membrane", "a_0": 8.0, "b_0": 6.0, "eta": 0.1, "rho0": 1e-6, "h0": {"val": 0.1}},
            },
        ],
        "neumann": [{"id": [2, 3], "dir": "normal_cur", "curve": 1}],
        "dirichlet": [{"id": [4], "dir": "y", "val": 0.0}, {"id": [5], "dir": "x", "val": 0.0}],
    }

    # problem setup
    problem = ambit_fe.ambit_main.Ambit(
        IO_PARAMS,
        TIME_PARAMS,
        SOLVER_PARAMS,
        [FEM_PARAMS_FLUID, FEM_PARAMS_ALE],
        [MATERIALS_FLUID, MATERIALS_ALE],
        [BC_DICT_FLUID, BC_DICT_ALE],
        time_curves=time_curves(),
        coupling_params=COUPLING_PARAMS,
        mor_params=ROM_PARAMS,
    )

    # problem solve
    problem.solve_problem()

    # --- results check
    tol = 1.0e-6

    check_node = []
    check_node.append(np.array([7.071068, 7.071068, 2.500000]))

    v_corr = np.zeros(3 * len(check_node))

    # correct results
    v_corr[0] = -2.0812855650931343e00  # x
    v_corr[1] = -2.0812855650931343e00  # y
    v_corr[2] = 0.0  # z

    check1 = ambit_fe.resultcheck.results_check_node(
        problem.mp.pbf.v, check_node, v_corr, problem.mp.pbf.V_v, problem.mp.comm, tol=tol, nm="v", readtol=1e-4
    )

    success = ambit_fe.resultcheck.success_check([check1], problem.mp.comm)

    if not success:
        raise RuntimeError("Test failed!")


if __name__ == "__main__":
    test_main()
