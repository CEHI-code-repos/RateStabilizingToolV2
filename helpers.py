import arcpy
import numpy as np
import pandas as pd
from param_updates import *

## Model helpers

# Calculate sets of contiguous adjacency structures
def get_islands(adj):
    f = set(range(len(adj)))
    isl_reg = []
    while f:
        active_list = {next(iter(f))}
        inactive_list = set()
        while active_list:
            Na = adj[active_list.pop()]
            active_list |= set(Na) - inactive_list
            inactive_list |= active_list
        isl_reg.append(sorted(inactive_list))
        f -= inactive_list
    return isl_reg

# Create initial values
def get_inits(Y, n, isl_reg, island_id):
    beta = np.array([logit(Y[i].sum(0) / n[i].sum(0)) for i in isl_reg])
    with np.errstate(divide = "ignore"):
        theta = logit(Y / n)
    theta[~np.isfinite(theta)] = beta[island_id][~np.isfinite(theta)]
    tau2, sig2 = [theta.var(0) * 2] * 2
    Z = theta - beta[island_id]
    return [theta, beta, Z, tau2, sig2]

# Create spatial data
def get_spdat(adj):
    if (min(min(adj)) == 1):
        adj = [list(np.array(adj[i]) - 1) for i in range(len(adj))]
    num_adj = [len(adj[i]) for i in range(len(adj))]
    isl_reg = get_islands(adj)
    num_island = len(isl_reg)
    num_island_region = [len(isl_reg[isl]) for isl in range(num_island)]
    island_id = [0] * len(adj)
    for isl in list(range(0, num_island)):
        island_id = [isl if i in isl_reg[isl] else x for i, x in enumerate(island_id)]
    return [adj, num_adj, isl_reg, num_island, num_island_region, island_id]

# Restricted UCAR Gibbs sampler
def gibbs_rucar(Y, n, adj, std_pop):
    np.random.seed(1) # For replicability
    adj, num_adj, isl_reg, num_island, num_island_region, island_id = get_spdat(adj) # Spatial data
    theta, beta, Z, tau2, sig2 = get_inits(Y, n, isl_reg, island_id) # Inits
    tau_a, tau_b, sigma_a, sigma_b = [1e-3] * 4 # Hyperparameters
    theta_sd = theta * 0 + 0.025
    theta_acpt = theta * 0
    num_region, num_group = Y.shape # Auxiliary variables
    m0 = 3
    A = Y.sum(0) / n.sum(0) * std_pop
    A = num_group * A / sum(A)
    theta_out = np.zeros([num_region, num_group, 400])
    arcpy.SetProgressor("step", "Generating estimates...", 0, 6000, 1)
    for s in range(6000):
        sig2 = sample_sig2(beta, Z, tau2, num_island_region, adj, num_adj, num_region, num_group, num_island, sigma_a, sigma_b, m0, A)
        tau2 = sample_tau2(theta, beta, Z, sig2, island_id, num_island_region, num_region, num_group, num_island, tau_a, tau_b, A, m0)
        beta = sample_beta(tau2, theta, Z, sig2, num_island_region, isl_reg, num_group, num_island, A, m0)
        Z = sample_Z(Z, tau2, sig2, theta, beta, num_adj, island_id, adj, num_region)
        theta, theta_acpt = sample_theta(theta, beta, Z, tau2, Y, n, theta_sd, theta_acpt, island_id, num_region, num_group)
        if (s + 1) % 100 == 0:
            theta_acpt = np.clip(theta_acpt, 0.20, 0.75)
            theta_sd  *= theta_acpt / 0.43
            theta_acpt *= 0
        if (s > 2000) & ((s + 1) % 10 == 0):
            theta_out[:, :, (s - 2000) // 10] = theta
        arcpy.SetProgressorPosition()
        print(str(round(s / 6000 * 100, 1)) + "%", end = "\r")
    print()
    return theta_out

# Age-standardization algorithm
def age_std(output, ages, std_pop, wtages):
    wt = std_pop[np.isin(ages, wtages)]
    wt = (wt / sum(wt)).reshape(1, len(wtages), 1)
    output_new = (output[:, np.where(np.isin(ages, wtages))[0], :] * wt).sum(1, keepdims = True)
    return np.concatenate((output, output_new), 1)

# ***MODIFIED*** Now outputs minimum CI for reliable estimates in each region, along with true/false value for reliability. Does not suppress estimates.
# Calculate medians
def get_medians(output, regions, ages):
    ci_values = [0.5, 0.75, 0.9, 0.95, 0.99]
    medians = pd.DataFrame(np.median(output, 2), regions, ages)
    ci_chart = np.zeros(medians.shape)
    for ci in ci_values:
        alpha = (1 - ci) / 2
        hi = np.quantile(output, 1 - alpha, 2)
        lo = np.quantile(output, alpha, 2)
        rp = medians / (hi - lo)
        ci_chart[rp >= 1] = ci
        if ci == 0.95:
            reliable = pd.DataFrame(rp >= 1, regions, ages)
    ci_chart = pd.DataFrame(ci_chart, regions, ages)
    if ages == [""]:
        medians = medians.rename(columns = {'': "median"})
        reliable = reliable.rename(columns = {'': "reliable"})
        ci_chart = ci_chart.rename(columns = {'': "max_reliable_ci"})
    return [medians, ci_chart, reliable]

## Tool helpers

const_age_grps = ["0-4", "5-14", "15-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75-84", "85up"]

def exists(url):
    if url is not None and arcpy.Exists(url):
        return True
    return False

def get_fieldType(url, fieldName):
    if exists(url) and fieldName is not None:
        age_groups_field = [f.type for f in arcpy.ListFields(url) if f.name == fieldName]
        if len(age_groups_field) == 1:
            return age_groups_field[0]
    return None
        
def get_pandas(url, fields):
    if exists(url) and fields is not None:
        return pd.DataFrame(data = arcpy.da.SearchCursor(url, fields), columns = fields)
    return None

def get_fieldAsList(url, fieldName):
    if exists(url) and fieldName is not None and get_fieldType(url, fieldName):
        return [val[0] for val in arcpy.da.SearchCursor(url, fieldName)]
    return None