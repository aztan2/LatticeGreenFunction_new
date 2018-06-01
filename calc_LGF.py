import time, scipy.io, argparse, logging
import numpy as np
import scipy.sparse.linalg as sla
import elastic
import setup
import IO_xyz


def setBC(i,grid,size_in,size_all,GEn,phi_R_grid,N,a0,V,t_mag,f):

    """
    Use the large R LGF (= EGF) to displace atoms in the "coupled outside"
    due to a point force on atom i in region 2.
    
    Parameters
    ----------
    i          : my atom index of the atom on which the point force is applied
    grid       : list of namedtuples containing atom info:
                 index, region, m-coord, n-coord, t-coord, basis 
    size_in    : number of atoms in regions 1+2+3+buffer
    size_all   : total number of atoms in the system, including the "coupled outside" region
    GEn        : list of complex ndarrays of fourier coefficients evaluated for each component of EGF
    phi_R_grid : list of angular terms in the real space large R LGF, computed for 
                 N equally-spaced angular (phi) values. Same as GEn, each entry in 
                 the list corresponds to the values for the different components of LGF
    N          : number of angular values for which the angular term in the real space large R LGF 
                 has been explicitly computed
    a0         : lattice constant in angstroms
    V          : unit cell volume
    t_mag      : magnitude of the periodic vector along the dislocation threading direction
    f          : array of shape (size_in,3) for forces
          
    Returns
    -------
    u : array of shape (size_all,3) for displacements of atoms
        in the far-field boundary region
    
    """ 
    
    u = np.zeros((size_all,3))  
    for atom_ff,u_ff in zip(grid[size_in:],u[size_in:]):
        ## rvec is the vector between the 2 atoms in terms of mnt
        rvec = np.array([grid[i].m,grid[i].n,grid[i].t]) - np.array([atom_ff.m,atom_ff.n,atom_ff.t])
        ## R is the R_perp in the mn plane
        R = a0*np.sqrt(rvec[0]**2 + rvec[1]**2)
        ## phi is the angle wrt +m axis
        phi = np.arctan2(rvec[1],rvec[0])
        if (abs(phi) > 1E-8):
            phi = phi%(2*np.pi)
        else:
            phi = 0.                
        ## given R and phi, calculate G(large R limit) 
        ## and use it to get displacements u = - G.f
        u_ff -= np.dot(elastic.G_largeR(GEn,phi_R_grid,R,phi,N,a0,V,t_mag),f[i]) 
        
    return u


if __name__ == '__main__':

                     
    parser = argparse.ArgumentParser(description='Computes the dislocation lattice Green function.')
    parser.add_argument('inputsfile',
                        help='text file that contains the crystal and dislocation setup info')
    parser.add_argument('atomxyzfile',
                        help='text file that contains the atom positions')
    parser.add_argument('Dfile',
                        help='.mtx file to read the FC matrix D from')
    parser.add_argument('Gfile',
                        help='.npy file to save the computed G to')
    parser.add_argument('logfile',
                        help='logfile to save to')
    parser.add_argument('-LGF_jmin',type=int,
                        help='(int) first atom index to compute LGF for',default=-1)
    parser.add_argument('-LGF_jmax',type=int,
                        help='(int) last atom index to compute LGF for',default=-1)
    
    
    ## read in the above arguments from command line
    args = parser.parse_args()   
    LGF_jmin = args.LGF_jmin
    LGF_jmax = args.LGF_jmax
    
    ## set up logging
    logging.basicConfig(filename=args.logfile,filemode='w',format='%(levelname)s:%(message)s', level=logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter('%(levelname)s:%(message)s'))
    logging.getLogger('').addHandler(console)
        
    ## read in setup details
    """
    crystalclass: crystal class (0=isotropic; 1=cubic; 2=hexagonal)    
    A           : 3x3 matrix for rotating from primitive cell basis to cartesian basis
                  (columns are unitcell vectors a1,a2,a3)
    unitcell_pos: positions of the basis atoms within the primitive unit cell
    a0          : lattice constant in angstroms
    Cijs        : list of Cijs
    M           : 3x3 matrix for rotating from mnt basis to cartesian basis
                  (columns are normalized m,n,t vectors)
    t_mag       : magnitude of the periodic vector along the dislocation threading direction
                  
    """
    with open(args.inputsfile,'r') as f1:
        crystalclass,A,unitcell_pos,a0,Cijs,M,t_mag = setup.readinputs(f1)
    V = a0**3 * np.dot(A[:,0],np.cross(A[:,1],A[:,2])) ## unit cell volume (Angstroms^3)
    
    ## read in grid of atoms
    """
    grid : list of namedtuples containing atom info
           [index, region, m-coord, n-coord, t-coord, basis]
    size_1,size_12,size_123,size_in,size_all: cumulative # atoms in each of the regions
    
    """   
    with open(args.atomxyzfile,'r') as f2:
        grid,[size_1,size_12,size_123,size_in] = IO_xyz.grid_from_xyz_reg(f2.read(),['Fe'],a0)
    size_all = len(grid)
    logging.info('System setup: size_1 = %d, size_12 = %d, size_123 = %d, size_in = %d, size_all = %d'
                  %(size_1,size_12,size_123,size_in,size_all))

    t0 = time.time()   

    ## load the big D matrix from file
    logging.info('loading D...')
    D = scipy.io.mmread(args.Dfile).tocsr()

    ## construct the 3x3x3x3 elastic stiffness tensor  
    ## convert elastic constants from GPa to eV/A^3
    C = elastic.convert_from_GPa(elastic.expand_C(elastic.construct_C(crystalclass,Cijs)))
    
    ## assemble the pieces necessary to evaluate the large R LGF, i.e. EGF
    ## based on the expression found in D.R. Trinkle, Phys. Rev. B 78, 014110 (2008)
    N = 256
    GEn = elastic.EGF_Fcoeffs(N,C,M,V)
    phi_R_grid = elastic.G_largeR_ang(GEn,N,N_max=int(N/2))

    ## compute the LGF matrix
    logging.info('Looping through atoms...')
    if LGF_jmin < 0: LGF_jmin = size_1   ## if LGF_jmin not specified, default = size_1
    if LGF_jmax < 0: LGF_jmax = size_12-1  ## if LGF_jmax not specified, default = size_12-1
    
    ## loop through every atom and every direction in reg 2
    for j in range(LGF_jmin,LGF_jmax+1):
        for d in range(0,3):
            f = np.zeros((3*size_in,))
            
            ## apply a force in an atom in reg 2
            f[j*3+d] = 1

            ## displace atoms in far-field boundary according to u_bc = -EGF.f(II)
            u_bc = setBC(j,grid,size_in,size_all,GEn,phi_R_grid,N,a0,V,t_mag,np.reshape(f,(size_in,3)))
            
            ## add the "correction forces" out in the buffer region
            ## f_eff = f(II) - (-D.(-EGF.f(II)) = f(II) + D.u_bc
            f += D.dot(np.reshape(u_bc,3*size_all))

            ## solve Dii.u = f_eff for u
            t1 = time.time()  
            [uf,conv] = sla.cg(D[0:3*size_in,0:3*size_in],f,tol=1e-08)
            logging.debug('%d, solve time: %f'%(conv,time.time()-t1))

            ## since I put in initial forces of unit magnitude,,
            ## the column vector uf = column of LGF matrix
            if ((j == LGF_jmin) and (d == 0)):
                G = uf[0:3*size_123].copy()
            else:
                G = np.column_stack((G,uf[0:3*size_123]))

            logging.info('Atom %d direction %d'%(j,d))

        np.save(args.Gfile, G) ## save updated G after every atom (3 columns)
        
    logging.info('COMPLETED !! Total time taken: %.5f'%(time.time()-t0))
   
     