#!/usr/bin/env python3

# Copyright (c) 2019-2023, Dr.-Ing. Marc Hirschvogel
# All rights reserved.

# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import sys, time

import numpy as np
from petsc4py import PETSc
from dolfinx import fem

from projection import project
from solver_utils import sol_utils
import preconditioner
import ioparams

### useful infos for PETSc mats, vecs, solvers...
# https://www.mcs.anl.gov/petsc/petsc4py-current/docs/apiref/petsc4py.PETSc.Mat-class.html
# https://www.mcs.anl.gov/petsc/petsc4py-current/docs/apiref/petsc4py.PETSc.Vec-class.html
# https://www.mcs.anl.gov/petsc/documentation/faq.html
# https://www.mcs.anl.gov/petsc/documentation/linearsolvertable.html
# https://www.mcs.anl.gov/petsc/petsc4py-current/docs/apiref/petsc4py.PETSc.KSP-class.html
# https://www.mcs.anl.gov/petsc/petsc4py-current/docs/apiref/petsc4py.PETSc.PC-class.html

# standard nonlinear solver for FEM problems
class solver_nonlinear:

    def __init__(self, pb, solver_params, cp=None):

        ioparams.check_params_solver(solver_params)

        self.comm = pb[0].comm

        self.pb = pb
        self.nprob = len(pb)

        self.x, self.is_ghosted = [[]]*self.nprob, [[]]*self.nprob
        self.nfields, self.ptype = [], []

        # problem variables list
        for npr in range(self.nprob):
            self.x[npr], self.is_ghosted[npr] = self.pb[npr].get_problem_var_list()
            self.nfields.append(len(self.x[npr]))
            self.ptype.append(self.pb[npr].problem_physics)

        self.set_solver_params(solver_params)

        # list of dicts for tolerances, residual, and increment norms
        self.tolerances, self.resnorms, self.incnorms = [], [], []
        # caution: [{}]*self.nprob actually would produce a list of dicts with same reference!
        # so do it like this...
        for npr in range(self.nprob):
            self.tolerances.append({})
            self.resnorms.append({})
            self.incnorms.append({})

        # set tolerances required by the user - may be a scalar, list, or list of lists
        for npr in range(self.nprob):
            for n in range(self.nfields[npr]):
                if isinstance(self.tolres, list):
                    if isinstance(self.tolres[npr], list):
                        self.tolerances[npr]['res'+str(n+1)] = self.tolres[npr][n]
                        self.tolerances[npr]['inc'+str(n+1)] = self.tolinc[npr][n]
                    else:
                        self.tolerances[npr]['res'+str(n+1)] = self.tolres[n]
                        self.tolerances[npr]['inc'+str(n+1)] = self.tolinc[n]
                else:
                    self.tolerances[npr]['res'+str(n+1)] = self.tolres
                    self.tolerances[npr]['inc'+str(n+1)] = self.tolinc

        self.initialize_petsc_solver()

        # sub-solver (for Lagrange-type constraints governed by a nonlinear system, e.g. 3D-0D coupling)
        if self.pb[0].sub_solve:
            self.subsol = solver_nonlinear_ode([self.pb[0].pb0], solver_params['subsolver_params'])
        else:
            self.subsol = None

        self.solutils = sol_utils(self)
        self.lsp = self.solutils.timestep_separator_len()

        if self.nprob>1:
            self.indlen = self.lsp+2
            self.lsp += 54

        self.li_s = [] # linear iterations over all solves

        self.cp = cp


    def set_solver_params(self, solver_params):

        try: self.maxiter = solver_params['maxiter']
        except: self.maxiter = 25

        try: self.divcont = solver_params['divergence_continue']
        except: self.divcont = None

        try: self.PTC = solver_params['ptc']
        except: self.PTC = False

        try: self.k_PTC_initial = solver_params['k_ptc_initial']
        except: self.k_PTC_initial = 0.1

        try: self.PTC_randadapt_range = solver_params['ptc_randadapt_range']
        except: self.PTC_randadapt_range = [0.85, 1.35]

        try: self.maxresval = solver_params['catch_max_res_value']
        except: self.maxresval = 1e16

        try: self.direct_solver = solver_params['direct_solver']
        except: self.direct_solver = 'mumps'

        try: self.iterative_solver = solver_params['iterative_solver']
        except: self.iterative_solver = 'gmres'

        try: self.precond_fields = solver_params['precond_fields']
        except: self.precond_fields = []

        try: self.fieldsplit_type = solver_params['fieldsplit_type']
        except: self.fieldsplit_type = 'jacobi'

        try: self.block_precond = solver_params['block_precond']
        except: self.block_precond = 'fieldsplit'

        try: self.tol_lin_rel = solver_params['tol_lin_rel']
        except: self.tol_lin_rel = 1.0e-5

        try: self.tol_lin_abs = solver_params['tol_lin_abs']
        except: self.tol_lin_abs = 1.0e-50

        try: self.res_lin_monitor = solver_params['res_lin_monitor']
        except: self.res_lin_monitor = 'rel'

        try: self.maxliniter = solver_params['max_liniter']
        except: self.maxliniter = 1200

        try: self.lin_norm_type = solver_params['lin_norm_type']
        except: self.lin_norm_type = 'unpreconditioned'

        if self.lin_norm_type=='preconditioned':
            self.linnormtype = 1
        elif self.lin_norm_type=='unpreconditioned':
            self.linnormtype = 2
        else:
            raise ValueError("Unknown lin_norm_type option!")

        try: self.print_liniter_every = solver_params['print_liniter_every']
        except: self.print_liniter_every = 1

        try: self.iset_options = solver_params['indexset_options']
        except: self.iset_options = {}
        is_option_keys = ['lms_to_p','lms_to_v','rom_to_new']
        # revert to defaults if not set by the user
        for k in is_option_keys:
            if k not in self.iset_options.keys(): self.iset_options[k] = False

        try: self.print_local_iter = solver_params['print_local_iter']
        except: self.print_local_iter = False

        try: self.tol_res_local = solver_params['tol_res_local']
        except: self.tol_res_local = 1.0e-10

        try: self.tol_inc_local = solver_params['tol_inc_local']
        except: self.tol_inc_local = 1.0e-10

        self.solvetype = solver_params['solve_type']

        self.tolres = solver_params['tol_res']
        self.tolinc = solver_params['tol_inc']

        self.r_list = [[]]*self.nprob
        self.del_u_ = [[]]*self.nprob


    def initialize_petsc_solver(self):

        self.ksp = [[]]*self.nprob

        for npr in range(self.nprob):

            # create solver
            self.ksp[npr] = PETSc.KSP().create(self.comm)

            if self.solvetype=='direct':

                self.ksp[npr].setType("preonly")
                self.ksp[npr].getPC().setType("lu")
                self.ksp[npr].getPC().setFactorSolverType(self.direct_solver)

            elif self.solvetype=='iterative':

                self.ksp[npr].setInitialGuessNonzero(False)
                self.ksp[npr].setNormType(self.linnormtype) # cf. https://www.mcs.anl.gov/petsc/petsc4py-current/docs/apiref/petsc4py.PETSc.KSP.NormType-class.html

                # block iterative method
                if self.nfields[npr] > 1:

                    self.ksp[npr].setType(self.iterative_solver) # cf. https://petsc.org/release/petsc4py/petsc4py.PETSc.KSP.Type-class.html

                    # TODO: how to use this adaptively...
                    #self.ksp.getPC().setReusePreconditioner(True)

                    if self.block_precond == 'fieldsplit':

                        # see e.g. https://petsc.org/main/manual/ksp/#sec-block-matrices
                        self.ksp[npr].getPC().setType("fieldsplit")
                        # cf. https://petsc.org/main/manualpages/PC/PCCompositeType

                        if self.fieldsplit_type=='jacobi':
                            splittype = PETSc.PC.CompositeType.ADDITIVE # block Jacobi
                        elif self.fieldsplit_type=='gauss_seidel':
                            splittype = PETSc.PC.CompositeType.MULTIPLICATIVE # block Gauss-Seidel
                        elif self.fieldsplit_type=='gauss_seidel_sym':
                            splittype = PETSc.PC.CompositeType.SYMMETRIC_MULTIPLICATIVE # symmetric block Gauss-Seidel
                        elif self.fieldsplit_type=='schur':
                            assert(self.nfields==2)
                            splittype = PETSc.PC.CompositeType.SCHUR # block Schur - for 2x2 block systems only
                        else:
                            raise ValueError("Unknown fieldsplit_type option.")

                        self.ksp[npr].getPC().setFieldSplitType(splittype)

                        iset = self.pb[npr].get_index_sets(isoptions=self.iset_options)
                        nsets = len(iset)

                        # normally, nsets = self.nfields, but for a surface-projected ROM (FrSI) problem, we have one more index set than fields
                        if nsets==2:   self.ksp[npr].getPC().setFieldSplitIS(("f1", iset[0]),("f2", iset[1]))
                        elif nsets==3: self.ksp[npr].getPC().setFieldSplitIS(("f1", iset[0]),("f2", iset[1]),("f3", iset[2]))
                        elif nsets==4: self.ksp[npr].getPC().setFieldSplitIS(("f1", iset[0]),("f2", iset[1]),("f3", iset[2]),("f4", iset[3]))
                        elif nsets==5: self.ksp[npr].getPC().setFieldSplitIS(("f1", iset[0]),("f2", iset[1]),("f3", iset[2]),("f4", iset[3]),("f5", iset[4]))
                        else: raise RuntimeError("Currently, no more than 5 fields/index sets are supported.")

                        # get the preconditioners for each block
                        ksp_fields = self.ksp[npr].getPC().getFieldSplitSubKSP()

                        assert(nsets==len(self.precond_fields)) # sanity check

                        # set field-specific preconditioners
                        for n in range(nsets):

                            if self.precond_fields[n]['prec'] == 'amg':
                                try: solvetype = self.precond_fields[n]['solve']
                                except: solvetype = "preonly"
                                ksp_fields[n].setType(solvetype)
                                try: amgtype = self.precond_fields[n]['amgtype']
                                except: amgtype = "hypre"
                                ksp_fields[n].getPC().setType(amgtype)
                                if amgtype=="hypre":
                                    ksp_fields[n].getPC().setHYPREType("boomeramg")
                            elif self.precond_fields[n]['prec'] == 'direct':
                                ksp_fields[n].setType("preonly")
                                ksp_fields[n].getPC().setType("lu")
                                ksp_fields[n].getPC().setFactorSolverType("mumps")
                            else:
                                raise ValueError("Currently, only either 'amg' or 'direct' are supported as field-specific preconditioner.")

                    elif self.block_precond == 'schur2x2':

                        self.ksp[npr].getPC().setType(PETSc.PC.Type.PYTHON)
                        bj = preconditioner.schur_2x2(self.pb[npr].get_index_sets(isoptions=self.iset_options),self.precond_fields,self.comm)
                        self.ksp[npr].getPC().setPythonContext(bj)

                    elif self.block_precond == 'simple2x2':

                        self.ksp[npr].getPC().setType(PETSc.PC.Type.PYTHON)
                        bj = preconditioner.simple_2x2(self.pb[npr].get_index_sets(isoptions=self.iset_options),self.precond_fields,self.comm)
                        self.ksp[npr].getPC().setPythonContext(bj)

                    elif self.block_precond == 'schur3x3':

                        self.ksp[npr].getPC().setType(PETSc.PC.Type.PYTHON)
                        bj = preconditioner.schur_3x3(self.pb[npr].get_index_sets(isoptions=self.iset_options),self.precond_fields,self.comm)
                        self.ksp[npr].getPC().setPythonContext(bj)

                    elif self.block_precond == 'schur4x4':

                        self.ksp[npr].getPC().setType(PETSc.PC.Type.PYTHON)
                        bj = preconditioner.schur_4x4(self.pb[npr].get_index_sets(isoptions=self.iset_options),self.precond_fields,self.comm)
                        self.ksp[npr].getPC().setPythonContext(bj)

                    elif self.block_precond == 'bgs2x2': # can also be called via PETSc's fieldsplit

                        self.ksp[npr].getPC().setType(PETSc.PC.Type.PYTHON)
                        bj = preconditioner.bgs_2x2(self.pb[npr].get_index_sets(isoptions=self.iset_options),self.precond_fields,self.comm)
                        self.ksp[npr].getPC().setPythonContext(bj)

                    elif self.block_precond == 'jacobi2x2': # can also be called via PETSc's fieldsplit

                        self.ksp[npr].getPC().setType(PETSc.PC.Type.PYTHON)
                        bj = preconditioner.jacobi_2x2(self.pb[npr].get_index_sets(isoptions=self.iset_options),self.precond_fields,self.comm)
                        self.ksp[npr].getPC().setPythonContext(bj)

                    else:
                        raise ValueError("Unknown block_precond option!")

                else:

                    if self.precond_fields[0] == 'amg':
                        self.ksp[npr].getPC().setType("hypre")
                        self.ksp[npr].getPC().setMGLevels(3)
                        self.ksp[npr].getPC().setHYPREType("boomeramg")
                    else:
                        raise ValueError("Currently, only 'amg' is supported as single-field preconditioner.")

                # set tolerances and print routine
                self.ksp[npr].setTolerances(rtol=self.tol_lin_rel, atol=self.tol_lin_abs, divtol=None, max_it=self.maxliniter)
                self.ksp[npr].setMonitor(lambda ksp, its, rnorm: self.solutils.print_linear_iter(its,rnorm))

                # set some additional PETSc options
                petsc_options = PETSc.Options()
                petsc_options.setValue('-ksp_gmres_modifiedgramschmidt', True)
                self.ksp[npr].setFromOptions()

            else:

                raise NameError("Unknown solvetype!")


    # solve for consistent initial acceleration a_old
    def solve_consistent_ini_acc(self, res_a, jac_aa, a_old):

        # create solver
        ksp = PETSc.KSP().create(self.comm)

        if self.solvetype=='direct':
            ksp.setType("preonly")
            ksp.getPC().setType("lu")
            ksp.getPC().setFactorSolverType(self.direct_solver)
        elif self.solvetype=='iterative':
            ksp.setType(self.iterative_solver)
            ksp.getPC().setType("hypre")
            ksp.getPC().setMGLevels(3)
            ksp.getPC().setHYPREType("boomeramg")
        else:
            raise NameError("Unknown solvetype!")

        # solve for consistent initial acceleration a_old
        M_a = fem.petsc.assemble_matrix(jac_aa, [])
        M_a.assemble()

        r_a = fem.petsc.assemble_vector(res_a)
        r_a.ghostUpdate(addv=PETSc.InsertMode.ADD, mode=PETSc.ScatterMode.REVERSE)

        ksp.setOperators(M_a)
        ksp.solve(-r_a, a_old.vector)

        a_old.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)

        r_a.destroy(), M_a.destroy()
        ksp.destroy()


    def solve_local(self, localdata):

        for l in range(len(localdata['var'])):
            self.newton_local(localdata['var'][l],localdata['res'][l],localdata['inc'][l],localdata['fnc'][l])


    def newton(self, t, localdata={}):

        # offset array for multi-field systems
        self.offsetarr = [[]]*self.nprob
        for npr in range(self.nprob):

            self.offsetarr[npr] = [0]
            off=0
            for n in range(self.nfields[npr]):
                if n==0:
                    if self.pb[npr].have_rom: # currently, ROM is only implemented for the first variable in the system!
                        off += self.pb[npr].rom.V.getLocalSize()[1]
                    else:
                        off += self.x[npr][0].getLocalSize()
                else:
                    off += self.x[npr][n].getLocalSize()

                self.offsetarr[npr].append(off)

        del_x, x_start = [], []
        for npr in range(self.nprob):

            del_x.append([[]]*self.nfields[npr])
            x_start.append([[]]*self.nfields[npr])

        for npr in range(self.nprob):

            for n in range(self.nfields[npr]):
                # solution increments for Newton
                del_x[npr][n] = self.x[npr][n].duplicate()
                del_x[npr][n].set(0.0)
                # start vector (needed for reset of Newton in case of divergence)
                x_start[npr][n] = self.x[npr][n].duplicate()
                self.x[npr][n].assemble()
                x_start[npr][n].axpby(1.0, 0.0, self.x[npr][n])
                if self.pb[npr].sub_solve: # can only be a 0D model so far...
                    s_start = self.pb[npr].pb0.s.duplicate()
                    self.pb[npr].pb0.s.assemble()
                    s_start.axpby(1.0, 0.0, self.pb[npr].pb0.s)

        # Newton iteration index
        it = 0
        # for PTC
        k_PTC = self.k_PTC_initial
        counter_adapt, max_adapt = 0, 10
        self.ni, self.li = 0, 0 # nonlinear and linear iteration counters

        for npr in range(self.nprob):

            if npr==0: ll=1
            else: ll=self.indlen

            self.solutils.print_nonlinear_iter(header=True, ptype=self.ptype[npr], prfxlen=ll)

            tes = time.time()

            # initial redidual actions due to predictor
            self.residual_problem_actions(t, npr, del_x, localdata)

            te = time.time() - tes

            self.solutils.print_nonlinear_iter(it, resnorms=self.resnorms[npr], te=te, ptype=self.ptype[npr], prfxlen=ll)

        it += 1

        while it < self.maxiter and counter_adapt < max_adapt:

            tes = time.time()

            converged, err = [], []

            # problem loop (in case of partitioned solves)
            for npr in range(self.nprob):

                # compute Jacobian
                K_list = self.pb[npr].assemble_stiffness(t, subsolver=self.subsol)

                if self.PTC:
                    # computes K_00 + k_PTC * I
                    K_list[0][0].shift(k_PTC)

                # model order reduction stuff - currently only on first mat in system...
                if self.pb[npr].have_rom:
                    # reduce Jacobian
                    self.pb[npr].rom.reduce_stiffness(K_list, self.nfields[npr])

                te = time.time() - tes

                # we use a block matrix (either with merge-into-one or for a nested iterative solver) if we have more than one field
                if self.nfields[npr] > 1:

                    tes = time.time()

                    # nested residual vector
                    r_full_nest = PETSc.Vec().createNest(self.r_list[npr])

                    # nested matrix
                    K_full_nest = PETSc.Mat().createNest(K_list, isrows=None, iscols=None, comm=self.comm)
                    K_full_nest.assemble()

                    te += time.time() - tes

                    # for monolithic direct solver
                    if self.solvetype=='direct':

                        tes = time.time()

                        K_full = PETSc.Mat()
                        K_full_nest.convert("aij", out=K_full)
                        K_full.assemble()

                        r_full = PETSc.Vec().createWithArray(r_full_nest.getArray())
                        r_full.assemble()

                        del_full = K_full.createVecLeft()
                        self.ksp[npr].setOperators(K_full)
                        te += time.time() - tes

                        tss = time.time()
                        self.ksp[npr].solve(-r_full, del_full)
                        ts = time.time() - tss

                    # for nested iterative solver
                    elif self.solvetype=='iterative':

                        tes = time.time()

                        # use same matrix as preconditioner
                        P_nest = K_full_nest

                        del_full = PETSc.Vec().createNest(del_x[npr])

                        # if index sets do not align with the nested matrix structure
                        # anymore, we need a merged matrix to extract the submats
                        if self.iset_options['rom_to_new'] or self.iset_options['lms_to_p'] or self.iset_options['lms_to_v']:
                            P = PETSc.Mat()
                            P_nest.convert("aij", out=P)
                            P.assemble()
                            P_nest = P

                        self.ksp[npr].setOperators(K_full_nest, P_nest)

                        r_full_nest.assemble()

                        # need to merge for non-fieldsplit-type preconditioners
                        if not self.block_precond == 'fieldsplit':
                            r_full = PETSc.Vec().createWithArray(r_full_nest.getArray())
                            r_full.assemble()
                            del_full = PETSc.Vec().createWithArray(del_full.getArray())
                            r_full_nest = r_full

                        te += time.time() - tes

                        tss = time.time()
                        self.ksp[npr].solve(-r_full_nest, del_full)
                        ts = time.time() - tss

                        self.solutils.print_linear_iter_last(self.ksp[npr].getIterationNumber(), self.ksp[npr].getResidualNorm(), self.ksp[npr].getConvergedReason())

                    else:

                        raise NameError("Unknown solvetype!")

                    for n in range(self.nfields[npr]):
                        del_x[npr][n].array[:] = del_full.array_r[self.offsetarr[npr][n]:self.offsetarr[npr][n+1]]

                else:

                    # solve linear system
                    self.ksp[npr].setOperators(K_list[0][0])

                    tss = time.time()
                    self.ksp[npr].solve(-self.r_list[npr][0], del_x[npr][0])
                    ts = time.time() - tss

                    if self.solvetype=='iterative':

                        self.solutils.print_linear_iter_last(self.ksp[npr].getIterationNumber(), self.ksp[npr].getResidualNorm(), self.ksp[npr].getConvergedReason())

                # get increment norm
                for n in range(self.nfields[npr]):
                    self.incnorms[npr]['inc'+str(n+1)] = del_x[npr][n].norm()

                # reconstruct full-length increment vector - currently only for first var!
                if self.pb[npr].have_rom:
                    del_x[npr][0] = self.pb[npr].rom.V.createVecLeft()
                    self.pb[npr].rom.V.mult(self.del_u_[npr], del_x[npr][0]) # V * dx_red

                # norm from last step for potential PTC adaption - prior to res update
                res_norm_main_last = self.resnorms[npr]['res1']

                # update variables
                for n in range(self.nfields[npr]):
                    self.x[npr][n].axpy(1.0, del_x[npr][n])
                    if self.is_ghosted[npr][n]==1:
                        self.x[npr][n].ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)
                    if self.is_ghosted[npr][n]==2:
                        subvecs = self.x[npr][n].getNestSubVecs()
                        for j in range(len(subvecs)): subvecs[j].ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)

                # destroy PETSc vecs...
                for n in range(self.nfields[npr]):
                    self.r_list[npr][n].destroy()

                # compute new residual actions after updated solution
                self.residual_problem_actions(t, npr, del_x, localdata)

                # for partitioned solves, we now have to update all dependent other residuals, too
                if self.nprob > 1:
                    for mpr in range(self.nprob):
                        if mpr!=npr:
                            for n in range(self.nfields[mpr]): self.r_list[mpr][n].destroy()
                            self.residual_problem_actions(t, mpr, del_x, localdata)

                if npr==0: ll=1
                else: ll=self.indlen

                self.solutils.print_nonlinear_iter(it, resnorms=self.resnorms[npr], incnorms=self.incnorms[npr], ts=ts, te=te, ptype=self.ptype[npr], prfxlen=ll)

                # destroy PETSc stuff...
                if self.nfields[npr] > 1:
                    r_full_nest.destroy(), K_full_nest.destroy(), del_full.destroy()
                    if self.solvetype=='direct': r_full.destroy(), K_full.destroy()
                    if self.solvetype=='iterative': P_nest.destroy()
                for n in range(self.nfields[npr]):
                    for m in range(self.nfields[npr]):
                        if K_list[n][m] is not None: K_list[n][m].destroy()

                # get converged state of each problem
                converged.append(self.solutils.check_converged(self.resnorms[npr], self.incnorms[npr], self.tolerances[npr], ptype=self.ptype[npr]))

                # for PTC - scale k_PTC with ratio of current to previous residual norm
                if self.PTC:
                    k_PTC *= self.resnorms[npr]['res1']/res_norm_main_last

                # adaptive PTC (for 3D block K_00 only!)
                if self.divcont=='PTC':

                    self.maxiter = 250 # should be enough...

                    # collect errors
                    err.append(self.solutils.catch_solver_errors(self.resnorms[npr]['res1'], incnorm=self.incnorms[npr]['inc1'], maxval=self.maxresval))

            # iteration update after all problems have been solved
            it += 1

            # now check if errors occurred
            if any(err):

                self.PTC = True
                # reset Newton step
                it, k_PTC = 1, self.k_PTC_initial

                # try a new (random) PTC parameter if even the solve with k_PTC_initial fails
                if counter_adapt>0:
                    k_PTC *= np.random.uniform(self.PTC_randadapt_range[0], self.PTC_randadapt_range[1])

                if self.comm.rank == 0:
                    print("PTC factor: %.4f" % (k_PTC))
                    sys.stdout.flush()

                for npr in range(self.nprob):

                    # reset solver
                    for n in range(self.nfields[npr]):
                        self.reset_step(self.x[npr][n], x_start[npr][n], self.is_ghosted[npr][n])
                        if self.pb[npr].sub_solve: # can only be a 0D model so far...
                            self.reset_step(self.pb.pb0.s, s_start, 0)

                    # destroy PETSc vecs...
                    for n in range(self.nfields[npr]):
                        self.r_list[npr][n].destroy()

                    # re-set residual actions
                    self.residual_problem_actions(t, npr, del_x, localdata)

                    counter_adapt += 1

            # check if all problems have converged
            if all(converged):
                # destroy PETSc vectors
                for npr in range(self.nprob):
                    for n in range(self.nfields[npr]):
                        del_x[npr][n].destroy(), x_start[npr][n].destroy(), self.r_list[npr][n].destroy()
                    if self.pb[npr].sub_solve: s_start.destroy()
                # reset to normal Newton if PTC was used in a divcont action
                if self.divcont=='PTC':
                    self.PTC = False
                    counter_adapt = 0
                self.ni = it-1
                break

        else:

            raise RuntimeError("Newton did not converge after %i iterations!" % (it))


    def residual_problem_actions(self, t, npr, del_x, localdata):

        # any local solve that is needed
        if self.pb[npr].localsolve:
            self.solve_local(localdata)

        # compute residual
        if self.cp is not None: self.cp.evaluate_residual_dbc_coupling()
        self.r_list[npr] = self.pb[npr].assemble_residual(t, subsolver=self.subsol)

        # reduce residual
        if self.pb[npr].have_rom:
            self.del_u_[npr] = self.pb[npr].rom.reduce_residual(self.r_list[npr], del_x[npr])

        # get residual norms
        for n in range(self.nfields[npr]):
            self.r_list[npr][n].assemble()
            self.resnorms[npr]['res'+str(n+1)] = self.r_list[npr][n].norm()


    def reset_step(self, vec, vec_start, ghosted):

        vec.axpby(1.0, 0.0, vec_start)

        if ghosted==1:
            vec.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)
        if ghosted==2:
            subvecs = vec.getNestSubVecs()
            for j in range(len(subvecs)): subvecs[j].ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)


    # local Newton where increment can be expressed as form at integration point level
    def newton_local(self, var, residual_forms, increment_forms, functionspaces, maxiter_local=20):

        it_local = 0

        num_loc_res = len(residual_forms)

        residuals, increments = [], []

        for i in range(num_loc_res):
            residuals.append(fem.Function(functionspaces[i]))
            increments.append(fem.Function(functionspaces[i]))

        res_norms, inc_norms = np.ones(num_loc_res), np.ones(num_loc_res)

        # return mapping scheme for nonlinear constitutive laws
        while it_local < maxiter_local:

            for i in range(num_loc_res):

                # interpolate symbolic increment form into increment vector
                increment_proj = project(increment_forms[i], functionspaces[i], self.pb[0].dx_)
                increments[i].vector.ghostUpdate(addv=PETSc.InsertMode.ADD, mode=PETSc.ScatterMode.REVERSE)
                increments[i].interpolate(increment_proj)

            for i in range(num_loc_res):
                # update var vector
                var[i].vector.axpy(1.0, increments[i].vector)
                var[i].vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)

            for i in range(num_loc_res):
                # interpolate symbolic residual form into residual vector
                residual_proj = project(residual_forms[i], functionspaces[i], self.pb[0].dx_)
                residuals[i].vector.ghostUpdate(addv=PETSc.InsertMode.ADD, mode=PETSc.ScatterMode.REVERSE)
                residuals[i].interpolate(residual_proj)
                # get residual and increment inf norms
                res_norms[i] = residuals[i].vector.norm(norm_type=3)
                inc_norms[i] = increments[i].vector.norm(norm_type=3)

            if self.print_local_iter:
                if self.comm.rank == 0:
                    print("      (it_local = %i, res: %.4e, inc: %.4e)" % (it_local,np.sum(res_norms),np.sum(inc_norms)))
                    sys.stdout.flush()

            # increase iteration index
            it_local += 1

            # check if converged
            if np.sum(res_norms) <= self.tol_res_local and np.sum(inc_norms) <= self.tol_inc_local:

                break

        else:

            raise RuntimeError("Local Newton did not converge after %i iterations!" % (it_local))



# solver for pure ODE (0D) problems (e.g. a system of first order ODEs integrated with One-Step-Theta method)
class solver_nonlinear_ode(solver_nonlinear):

    def __init__(self, pb, solver_params):

        ioparams.check_params_solver(solver_params)

        self.comm = pb[0].comm

        self.pb = pb[0] # only one problem considered here
        self.nprob = 1

        self.ptype = self.pb.problem_physics

        try: self.maxiter = solver_params['maxiter']
        except: self.maxiter = 25

        try: self.direct_solver = solver_params['direct_solver']
        except: self.direct_solver = 'mumps'

        self.tolres = solver_params['tol_res']
        self.tolinc = solver_params['tol_inc']

        self.tolerances = [[]]
        self.tolerances[0] = {'res1' : self.tolres, 'inc1' : self.tolinc}

        # dicts for residual and increment norms
        self.resnorms, self.incnorms = {}, {}

        self.PTC = False # don't think we'll ever need PTC for the 0D ODE problem...
        self.solvetype = 'direct' # only a direct solver is available for ODE problems

        self.solutils = sol_utils(self)

        self.lsp = self.solutils.timestep_separator_len()

        self.initialize_petsc_solver()


    def initialize_petsc_solver(self):

        self.ksp = [[]]

        # create solver
        self.ksp[0] = PETSc.KSP().create(self.comm)
        self.ksp[0].setType("preonly")
        self.ksp[0].getPC().setType("lu")
        self.ksp[0].getPC().setFactorSolverType(self.direct_solver)


    def newton(self, t, print_iter=True, sub=False):

        # Newton iteration index
        it = 0

        if print_iter: self.solutils.print_nonlinear_iter(header=True, sub=sub, ptype=self.ptype)

        self.ni, self.li = 0, 0 # nonlinear and linear iteration counters (latter probably never relevant for ODE problems...)

        tes = time.time()

        # compute initial residual
        r = self.pb.assemble_residual(t)

        # get initial residual norm
        self.resnorms['res1'] = r.norm()

        te = time.time() - tes

        if print_iter: self.solutils.print_nonlinear_iter(it, resnorms=self.resnorms, te=te, sub=sub, ptype=self.ptype)

        it += 1

        while it < self.maxiter:

            tes = time.time()

            # compute Jacobian
            K = self.pb.assemble_stiffness(t)

            ds = K.createVecLeft()

            # solve linear system
            self.ksp[0].setOperators(K)

            te = time.time() - tes

            tss = time.time()
            self.ksp[0].solve(-r, ds)
            ts = time.time() - tss

            # update solution
            self.pb.s.axpy(1.0, ds)

            r.destroy()

            # compute new residual after updated solution
            r = self.pb.assemble_residual(t)

            # get norms
            self.resnorms['res1'], self.incnorms['inc1'] = r.norm(), ds.norm()

            if print_iter: self.solutils.print_nonlinear_iter(it, resnorms=self.resnorms, incnorms=self.incnorms, ts=ts, te=te, sub=sub, ptype=self.ptype)

            # destroy PETSc stuff...
            ds.destroy(), K.destroy()

            it += 1

            # check if converged
            converged = self.solutils.check_converged(self.resnorms, self.incnorms, self.tolerances[0], ptype='flow0d')
            if converged:
                if print_iter and sub:
                    if self.comm.rank == 0:
                        print('       ****************************************************\n')
                        sys.stdout.flush()
                self.ni = it-1
                break

        else:

            raise RuntimeError("Newton for ODE system did not converge after %i iterations!" % (it))
