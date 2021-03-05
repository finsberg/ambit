#!/usr/bin/env python3

# Copyright (c) 2019-2021, Dr.-Ing. Marc Hirschvogel
# All rights reserved.

# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import time, sys, math
import numpy as np

from petsc4py import PETSc
import timeintegration
import utilities
import solver_nonlin

from base import problem_base

# framework of 0D flow models, relating pressure p (and its derivative) to fluxes q

class Flow0DProblem(problem_base):

    def __init__(self, io_params, time_params, model_params, time_curves, coupling_params={}, comm=None):
        problem_base.__init__(self, io_params, time_params, comm)
        
        self.problem_physics = 'flow0d'
        
        self.time_params = time_params
        
        self.simname = io_params['simname']
        
        try: chamber_models = model_params['chamber_models']
        except: chamber_models = {'lv' : '0D_elast', 'rv' : '0D_elast', 'la' : '0D_elast', 'ra' : '0D_elast'}
        
        try: chamber_interfaces = model_params['chamber_interfaces']
        except: chamber_interfaces = {'lv' : 1, 'rv' : 1, 'la' : 1, 'ra' : 1}
        
        try: prescribed_path = io_params['prescribed_path']
        except: prescribed_path = None

        try: initial_file = time_params['initial_file']
        except: initial_file = ''

        # could use extra write frequency setting for 0D model (i.e. for coupled problem)
        try: self.write_results_every_0D = io_params['write_results_every_0D']
        except: self.write_results_every_0D = io_params['write_results_every']

        # for restart
        try: self.write_restart_every = io_params['write_restart_every']
        except: self.write_restart_every = -1

        # could use extra output path setting for 0D model (i.e. for coupled problem)
        try: self.output_path_0D = io_params['output_path_0D']
        except: self.output_path_0D = io_params['output_path']
        
        try: have_elastance = model_params['have_elastance']
        except: have_elastance = False
        
        try: valvelaw = model_params['valvelaw']
        except: valvelaw = 'pwlin_pres'

        try: self.cq = coupling_params['coupling_quantity']
        except: self.cq = 'volume'
        
        try: self.eps_periodic = time_params['eps_periodic']
        except: self.eps_periodic = 1.0e-20
        
        try: self.periodic_checktype = time_params['periodic_checktype']
        except: self.periodic_checktype = 'allvar'
        
        try: self.prescribed_variables = model_params['prescribed_variables']
        except: self.prescribed_variables = {}
        
        try: self.perturb_type = model_params['perturb_type']
        except: self.perturb_type = None
        
        try: self.perturb_after_cylce = model_params['perturb_after_cylce']
        except: self.perturb_after_cylce = -1
        # definitely set to -1 if we don't have a perturb type
        if self.perturb_type is None: self.perturb_after_cylce = -1

        # initialite 0D model class
        if model_params['modeltype'] == '2elwindkessel':
            from cardiovascular0D_2elwindkessel import cardiovascular0D2elwindkessel
            self.cardvasc0D = cardiovascular0D2elwindkessel(time_params['theta_ost'], model_params['parameters'], cq=self.cq, comm=self.comm)
        elif model_params['modeltype'] == '4elwindkesselLsZ':
            from cardiovascular0D_4elwindkesselLsZ import cardiovascular0D4elwindkesselLsZ
            self.cardvasc0D = cardiovascular0D4elwindkesselLsZ(time_params['theta_ost'], model_params['parameters'], cq=self.cq, comm=self.comm)
        elif model_params['modeltype'] == '4elwindkesselLpZ':
            from cardiovascular0D_4elwindkesselLpZ import cardiovascular0D4elwindkesselLpZ
            self.cardvasc0D = cardiovascular0D4elwindkesselLpZ(time_params['theta_ost'], model_params['parameters'], cq=self.cq, comm=self.comm)
        elif model_params['modeltype'] == 'syspul':
            from cardiovascular0D_syspul import cardiovascular0Dsyspul
            self.cardvasc0D = cardiovascular0Dsyspul(time_params['theta_ost'], model_params['parameters'], chmodels=chamber_models, chinterf=chamber_interfaces, prescrpath=prescribed_path, have_elast=have_elastance, cq=self.cq, valvelaw=valvelaw, comm=self.comm)
        elif model_params['modeltype'] == 'syspulcap':
            from cardiovascular0D_syspulcap import cardiovascular0Dsyspulcap
            self.cardvasc0D = cardiovascular0Dsyspulcap(time_params['theta_ost'], model_params['parameters'], chmodels=chamber_models, chinterf=chamber_interfaces, prescrpath=prescribed_path, have_elast=have_elastance, cq=self.cq, valvelaw=valvelaw, comm=self.comm)
        elif model_params['modeltype'] == 'syspulcap2':
            from cardiovascular0D_syspulcap import cardiovascular0Dsyspulcap2
            self.cardvasc0D = cardiovascular0Dsyspulcap2(time_params['theta_ost'], model_params['parameters'], chmodels=chamber_models, chinterf=chamber_interfaces, prescrpath=prescribed_path, have_elast=have_elastance, cq=self.cq, valvelaw=valvelaw, comm=self.comm)
        elif model_params['modeltype'] == 'syspulcaprespir':
            from cardiovascular0D_syspulcaprespir import cardiovascular0Dsyspulcaprespir
            self.cardvasc0D = cardiovascular0Dsyspulcaprespir(time_params['theta_ost'], model_params['parameters'], chmodels=chamber_models, chinterf=chamber_interfaces, prescrpath=prescribed_path, have_elast=have_elastance, cq=self.cq, valvelaw=valvelaw, comm=self.comm)
        else:
            raise NameError("Unknown 0D modeltype!")

        # vectors and matrices
        self.K = PETSc.Mat().createAIJ(size=(self.cardvasc0D.numdof,self.cardvasc0D.numdof), bsize=None, nnz=None, csr=None, comm=self.comm)
        self.K.setUp()
        
        self.s, self.s_old, self.s_mid = self.K.createVecLeft(), self.K.createVecLeft(), self.K.createVecLeft()
        self.sTc, self.sTc_old = self.K.createVecLeft(), self.K.createVecLeft()
        
        self.df, self.df_old = self.K.createVecLeft(), self.K.createVecLeft()
        self.f, self.f_old   = self.K.createVecLeft(), self.K.createVecLeft()

        self.aux, self.aux_old, self.aux_mid = np.zeros(self.cardvasc0D.numdof), np.zeros(self.cardvasc0D.numdof), np.zeros(self.cardvasc0D.numdof)
        
        self.s_set = self.K.createVecLeft() # set point for multisale analysis
        
        self.c = []

        # initialize flow0d time-integration class
        self.ti = timeintegration.timeintegration_flow0d(time_params, time_curves, self.t_init, self.comm)

        if initial_file:
            initialconditions = self.cardvasc0D.set_initial_from_file(initial_file)
        else:
            initialconditions = time_params['initial_conditions']

        self.cardvasc0D.initialize(self.s, initialconditions)
        self.cardvasc0D.initialize(self.s_old, initialconditions)
        self.cardvasc0D.initialize(self.sTc_old, initialconditions)

        self.theta_ost = time_params['theta_ost']


    def writerestart(self, sname, N, ms=False):
        
        self.cardvasc0D.write_restart(self.output_path_0D, sname+'_s', N, self.s)
        self.cardvasc0D.write_restart(self.output_path_0D, sname+'_aux', N, self.aux)
        self.cardvasc0D.write_restart(self.output_path_0D, sname+'_sTc_old', N, self.sTc_old)
        if ms: self.cardvasc0D.write_restart(self.output_path_0D, sname+'_s_set', N, self.s_set)
        
        if self.cardvasc0D.T_cycl > 0: # write heart cycle info
            if self.comm.rank == 0:
                filename = self.output_path_0D+'/checkpoint_'+sname+'_cycledata_'+str(N)+'.txt'
                f = open(filename, 'wt')
                f.write('%i %.8f' % (self.ti.cycle[0],self.ti.cycleerror[0]))
                f.close()


    def readrestart(self, sname, rst, ms=False):
        
        self.cardvasc0D.read_restart(self.output_path_0D, sname+'_s', rst, self.s)
        self.cardvasc0D.read_restart(self.output_path_0D, sname+'_s', rst, self.s_old)
        self.cardvasc0D.read_restart(self.output_path_0D, sname+'_aux', rst, self.aux)
        self.cardvasc0D.read_restart(self.output_path_0D, sname+'_aux', rst, self.aux_old)
        self.cardvasc0D.read_restart(self.output_path_0D, sname+'_sTc_old', rst, self.sTc_old)
        if ms: self.cardvasc0D.read_restart(self.output_path_0D, sname+'_s_set', rst, self.s_set)

        if self.cardvasc0D.T_cycl > 0: # read heart cycle info
            self.ti.cycle[0] = np.loadtxt(self.output_path_0D+'/checkpoint_'+sname+'_cycledata_'+str(rst)+'.txt', usecols=(0), dtype=int)
            self.ti.cycleerror[0] = np.loadtxt(self.output_path_0D+'/checkpoint_'+sname+'_cycledata_'+str(rst)+'.txt', usecols=(1), dtype=float)
            self.t_init -= (self.ti.cycle[0]-1) * self.cardvasc0D.T_cycl



class Flow0DSolver():

    def __init__(self, problem, solver_params):
    
        self.pb = problem

        self.solver_params = solver_params

        # initialize nonlinear solver class
        self.solnln = solver_nonlin.solver_nonlinear_0D(self.pb, self.solver_params)
        

    def solve_problem(self):
        
        start = time.time()
        
        # print header
        utilities.print_problem(self.pb.problem_type, self.pb.comm, self.pb.cardvasc0D.numdof)

        # read restart information
        if self.pb.restart_step > 0:
            self.pb.readrestart(self.pb.simname, self.pb.restart_step)
            self.pb.simname += '_r'+str(self.pb.restart_step)

        # evaluate old state
        if self.pb.ti.time_curves is not None:
            self.pb.c.append(self.pb.ti.timecurves(1)(self.pb.t_init))
        
        self.pb.cardvasc0D.evaluate(self.pb.s_old, self.pb.dt, self.pb.t_init, self.pb.df_old, self.pb.f_old, None, self.pb.c, self.pb.aux_old)


        # flow 0d main time loop
        for N in range(self.pb.restart_step+1, self.pb.numstep+1):
            
            wts = time.time()
            
            # current time
            t = N * self.pb.dt

            # offset time for multiple cardiac cycles
            t_off = (self.pb.ti.cycle[0]-1) * self.pb.cardvasc0D.T_cycl # zero if T_cycl variable is not specified

            # external volume/flux from time curve
            if self.pb.ti.time_curves is not None: self.pb.c[0] = self.pb.ti.timecurves(1)(t-t_off)

            # solve
            self.solnln.newton(self.pb.s, t-t_off)

            # get midpoint dof values for post-processing (has to be called before update!)
            self.pb.cardvasc0D.midpoint_avg(self.pb.s, self.pb.s_old, self.pb.s_mid), self.pb.cardvasc0D.midpoint_avg(self.pb.aux, self.pb.aux_old, self.pb.aux_mid)

            # update timestep
            self.pb.cardvasc0D.update(self.pb.s, self.pb.df, self.pb.f, self.pb.s_old, self.pb.df_old, self.pb.f_old, self.pb.aux, self.pb.aux_old)
            
            # print to screen
            self.pb.cardvasc0D.print_to_screen(self.pb.s_mid,self.pb.aux_mid)

            # solve time for time step
            wte = time.time()
            wt = wte - wts

            # print time step info to screen
            self.pb.ti.print_timestep(N, t, self.pb.numstep, wt=wt)
            
            # check for periodicity in cardiac cycle and stop if reached (only for syspul* models - cycle counter gets updated here)
            is_periodic = self.pb.cardvasc0D.cycle_check(self.pb.s, self.pb.sTc, self.pb.sTc_old, t-t_off, self.pb.ti.cycle, self.pb.ti.cycleerror, self.pb.eps_periodic, check=self.pb.periodic_checktype, inioutpath=self.pb.output_path_0D, nm=self.pb.simname, induce_pert_after_cycl=self.pb.perturb_after_cylce)

            # induce some disease/perturbation for cardiac cycle (i.e. valve stenosis or leakage)
            if self.pb.perturb_type is not None: self.pb.cardvasc0D.induce_perturbation(self.pb.perturb_type, self.pb.ti.cycle[0], self.pb.perturb_after_cylce)

            # raw txt file output of 0D model quantities
            if self.pb.write_results_every_0D > 0 and N % self.pb.write_results_every_0D == 0:
                self.pb.cardvasc0D.write_output(self.pb.output_path_0D, t, self.pb.s_mid, self.pb.aux_mid, self.pb.simname)
            # write 0D restart info - old and new quantities are the same at this stage (except cycle values sTc)
            if self.pb.write_restart_every > 0 and N % self.pb.write_restart_every == 0:
                self.pb.writerestart(self.pb.simname, N)

            if is_periodic:
                if self.pb.comm.rank == 0:
                    print("Periodicity reached after %i heart cycles with cycle error %.4f! Finished. :-)" % (self.pb.ti.cycle[0]-1,self.pb.ti.cycleerror[0]))
                    sys.stdout.flush()
                break
            
            # maximum number of steps to perform
            try:
                if N == self.pb.pbs.numstep_stop:
                    break
            except:
                pass


        if self.pb.comm.rank == 0: # only proc 0 should print this
            print('Time for computation: %.4f s (= %.2f min)' % ( time.time()-start, (time.time()-start)/60. ))
            sys.stdout.flush()
