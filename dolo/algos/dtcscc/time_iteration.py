import time

import numpy as np

from dolo.algos.dtcscc.perturbations import approximate_controls
from dolo.numeric.optimize.ncpsolve import ncpsolve
from dolo.numeric.optimize.newton import (SerialDifferentiableFunction,
                                          serial_newton)
from dolo.numeric.interpolation import create_interpolator


def time_iteration(model, verbose=False, initial_dr=None,
                   pert_order=1, with_complementarities=True,
                   grid={}, distribution={},
                   maxit=500, tol=1e-8, inner_maxit=10,
                   hook=None):
    '''
    Finds a global solution for ``model`` using backward time-iteration.

    This algorithm iterates on the residuals of the arbitrage equations

    Parameters
    ----------
    model : NumericModel
        "dtcscc" model to be solved
    verbose : boolean
        if True, display iterations
    initial_dr : decision rule
        initial guess for the decision rule
    pert_order : {1}
        if no initial guess is supplied, the perturbation solution at order
        ``pert_order`` is used as initial guess
    with_complementarities : boolean (True)
        if False, complementarity conditions are ignored
    grid: grid options
    distribution: distribution options
    maxit: maximum number of iterations
    inner_maxit: maximum number of iteration for inner solver
    tol: tolerance criterium for successive approximations

    Returns
    -------
    decision rule :
        approximated solution
    '''

    def vprint(t):
        if verbose:
            print(t)

    parms = model.calibration['parameters']


    approx = model.get_grid(**grid)
    interp_type = approx.interpolation
    dr = create_interpolator(approx, approx.interpolation)

    distrib = model.get_distribution(**distribution)
    sigma = distrib.sigma
    epsilons, weights = distrib.discretize()

    if initial_dr is None:
        if pert_order == 1:
            initial_dr = approximate_controls(model)

        if pert_order > 1:
            raise Exception("Perturbation order > 1 not supported (yet).")

    vprint('Starting time iteration')

    # TODO: transpose

    grid = dr.grid

    xinit = initial_dr(grid)
    xinit = xinit.real  # just in case...

    f = model.functions['arbitrage']
    g = model.functions['transition']

    # define objective function (residuals of arbitrage equations)
    def fun(x):
        return step_residual(grid, x, dr, f, g, parms, epsilons, weights)

    ##
    t1 = time.time()
    err = 1
    x0 = xinit
    it = 0

    verbit = True if verbose == 'full' else False

    if with_complementarities:
        lbfun = model.functions['controls_lb']
        ubfun = model.functions['controls_ub']
        lb = lbfun(grid, parms)
        ub = ubfun(grid, parms)
    else:
        lb = None
        ub = None

    if verbose:
        headline = '|{0:^4} | {1:10} | {2:8} | {3:8} | {4:3} |'
        headline = headline.format('N', ' Error', 'Gain', 'Time', 'nit')
        stars = '-'*len(headline)
        print(stars)
        print(headline)
        print(stars)

        # format string for within loop
        fmt_str = '|{0:4} | {1:10.3e} | {2:8.3f} | {3:8.3f} | {4:3} |'

    err_0 = 1

    while err > tol and it < maxit:
        # update counters
        t_start = time.time()
        it += 1

        # update interpolation coefficients (NOTE: filters through `fun`)
        dr.set_values(x0)

        # Derivative of objective function
        sdfun = SerialDifferentiableFunction(fun)

        # Apply solver with current decision rule for controls
        if with_complementarities:
            [x, nit] = ncpsolve(sdfun, lb, ub, x0, verbose=verbit,
                                maxit=inner_maxit)
        else:
            [x, nit] = serial_newton(sdfun, x0, verbose=verbit)

        # update error and print if `verbose`
        err = abs(x-x0).max()
        err_SA = err/err_0
        err_0 = err
        t_finish = time.time()
        elapsed = t_finish - t_start
        if verbose:
            print(fmt_str.format(it, err, err_SA, elapsed, nit))

        # Update control vector
        x0[:] = x  # x0 = x0 + (x-x0)

        # call user supplied hook, if any
        if hook:
            hook(dr, it, err)

        # warn and bail if we get inf
        if False in np.isfinite(x0):
            print('iteration {} failed : non finite value')
            return [x0, x]

    if it == maxit:
        import warnings
        warnings.warn(UserWarning("Maximum number of iterations reached"))

    # compute final fime and do final printout if `verbose`
    t2 = time.time()
    if verbose:
        print(stars)
        print('Elapsed: {} seconds.'.format(t2 - t1))
        print(stars)

    return dr


def step_residual(s, x, dr, f, g, parms, epsilons, weights):
    """
    Comptue the residuals of the arbitrage equaitons.

    Recall that the arbitrage equations have the form

        0 = E_t [f(...)]

    This function computes and returns the right hand side.
    """

    # TODO: transpose
    n_draws = epsilons.shape[0]
    [N, n_x] = x.shape
    ss = np.tile(s, (n_draws, 1))
    xx = np.tile(x, (n_draws, 1))
    ee = np.repeat(epsilons, N, axis=0)

    # evaluate transition (g) to update state
    ssnext = g(ss, xx, ee, parms)
    xxnext = dr(ssnext)  # evaluate decision rule (dr) to update controls

    # evaluate arbitrage/Euler equations (f) to compute values
    val = f(ss, xx, ee, ssnext, xxnext, parms)

    # apply quadrature to compute implicit expectation in arbitrage equations
    res = np.zeros((N, n_x))
    for i in range(n_draws):
        res += weights[i] * val[N*i:N*(i+1), :]

    return res


def test_residuals(s, dr, f, g, parms, epsilons, weights):

    n_draws = epsilons.shape[1]

    n_g = s.shape[1]
    x = dr(s)
    n_x = x.shape[0]

    ss = np.tile(s, (1, n_draws))
    xx = np.tile(x, (1, n_draws))
    ee = np.repeat(epsilons, n_g, axis=1)

    ssnext = g(ss, xx, ee, parms)
    xxnext = dr(ssnext)
    val = f(ss, xx, ee, ssnext, xxnext, parms)

    errors = np.zeros((n_x, n_g))
    for i in range(n_draws):
        errors += weights[i] * val[:, n_g*i:n_g*(i+1)]

    squared_errors = np.power(errors, 2)
    std_errors = np.sqrt(np.sum(squared_errors, axis=0)/len(squared_errors))

    return std_errors

import time
import numpy as np
from dolo.numeric.discretization import gauss_hermite_nodes
from dolo.numeric.interpolation.splines import MultivariateCubicSplines
from dolo.numeric.misc import mlinspace
from dolo.algos.dtcscc.perturbations import approximate_controls

def time_iteration_direct(model, maxit=100, grid={}, distribution={}, tol=1e-8, initial_dr=None, verbose=False):

    t1 = time.time()

    g = model.functions['transition']
    d = model.functions['direct_response']
    h = model.functions['expectation']

    p = model.calibration['parameters']

    if initial_dr is None:
        drp = approximate_controls(model)
    else:
        drp = initial_dr

    approx = model.get_grid(**grid)
    grid = approx.grid
    interp_type = approx.interpolation
    dr = create_interpolator(approx, approx.interpolation)

    distrib = model.get_distribution(**distribution)
    nodes, weights = distrib.discretize()

    N = grid.shape[0]
    z = np.zeros((N,len(model.symbols['expectations'])))

    x_0 = drp(grid)

    it = 0
    err = 10
    err_0 = 10

    if verbose:
        headline = '|{0:^4} | {1:10} | {2:8} | {3:8} |'
        headline = headline.format('N', ' Error', 'Gain', 'Time')
        stars = '-'*len(headline)
        print(stars)
        print(headline)
        print(stars)

        # format string for within loop
        fmt_str = '|{0:4} | {1:10.3e} | {2:8.3f} | {3:8.3f} |'

    while err>tol and it<=maxit:

        t_start = time.time()

        dr.set_values(x_0)

        z[...] = 0
        for i in range(weights.shape[0]):
            e = nodes[i,:]
            S = g(grid, x_0, e, p)
            # evaluate future controls
            X = dr(S)
            z += weights[i]*h(S,X,p)

        # TODO: check that control is admissible
        new_x = d(grid, z, p)

        # check whether they differ from the preceding guess
        err = (abs(new_x - x_0).max())

        x_0 = new_x

        if verbose:

            # update error and print if `verbose`
            err_SA = err/err_0
            err_0 = err
            t_finish = time.time()
            elapsed = t_finish - t_start
            if verbose:
                print(fmt_str.format(it, err, err_SA, elapsed))


    if it == maxit:
        import warnings
        warnings.warn(UserWarning("Maximum number of iterations reached"))

    # compute final fime and do final printout if `verbose`
    t2 = time.time()
    if verbose:
        print(stars)
        print('Elapsed: {} seconds.'.format(t2 - t1))
        print(stars)

    return dr
