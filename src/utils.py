import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from data_pipeline import tf_tg_interactions, load_data
import scipy.stats
from statsmodels.stats.multitest import multipletests
from clustering import Cluster
from sklearn.metrics import silhouette_score
from scipy.cluster.hierarchy import linkage, cophenet


# ---------------------
# CORRELATION UTILITIES
# ---------------------

def pearson_correlation(x, y):
    """
    Computes similarity measure between each pair of genes in the bipartite graph x <-> y
    :param x: Gene matrix 1. Shape=(nb_samples, nb_genes_1)
    :param y: Gene matrix 2. Shape=(nb_samples, nb_genes_2)
    :return: Matrix with shape (nb_genes_1, nb_genes_2) containing the similarity coefficients
    """

    def standardize(a):
        a_off = np.mean(a, axis=0)
        a_std = np.std(a, axis=0)
        return (a - a_off) / a_std

    assert x.shape[0] == y.shape[0]
    x_ = standardize(x)
    y_ = standardize(y)
    return np.dot(x_.T, y_) / x.shape[0]


def cosine_similarity(x, y):
    """
    Computes cosine similarity between vectors x and y
    :param x: Array of numbers. Shape=(n,)
    :param y: Array of numbers. Shape=(n,)
    :return: cosine similarity between vectors
    """
    return np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y))


def upper_diag_list(m_):
    """
    Returns the condensed list of all the values in the upper-diagonal of m_
    :param m_: numpy array of float. Shape=(N, N)
    :return: list of values in the upper-diagonal of m_ (from top to bottom and from
             left to right). Shape=(N*(N-1)/2,)
    """
    m = np.triu(m_, k=1)  # upper-diagonal matrix
    tril = np.zeros_like(m_) + np.nan
    tril = np.tril(tril)
    m += tril
    m = np.ravel(m)
    return m[~np.isnan(m)]


def correlations_list(x, y, corr_fun=pearson_correlation):
    """
    Generates correlation list between all pairs of genes in the bipartite graph x <-> y
    :param x: Gene matrix 1. Shape=(nb_samples, nb_genes_1)
    :param y: Gene matrix 2. Shape=(nb_samples, nb_genes_2)
    :param corr_fun: correlation function taking x and y as inputs
    """
    corr = corr_fun(x, y)
    return upper_diag_list(corr)


def compute_tf_tg_corrs(expr, gene_symbols, tf_tg=None, flat=True):
    """
    Computes the lists of TF-TG and TG-TG correlations
    :param expr: matrix of gene expressions. Shape=(nb_samples, nb_genes)
    :param gene_symbols: list of gene symbols matching the expr matrix. Shape=(nb_genes,)
    :param tf_tg: dict with TF symbol as key and list of TGs' symbols as value
    :param flat: whether to return flat lists
    :return: lists of TF-TG and TG-TG correlations, respectively
    """
    if tf_tg is None:
        tf_tg = tf_tg_interactions()
    gene_symbols = np.array(gene_symbols)

    tf_tg_corr = []
    tg_tg_corr = []
    for tf, tgs in tf_tg.items():
        tg_idxs = np.array([np.where(gene_symbols == tg)[0] for tg in tgs if tg in gene_symbols]).ravel()

        if tf in gene_symbols and len(tg_idxs) > 0:
            # TG-TG correlations
            expr_tgs = expr[:, tg_idxs]
            corr = correlations_list(expr_tgs, expr_tgs)
            tg_tg_corr += [corr.tolist()]

            # TF-TG correlations
            tf_idx = np.argwhere(gene_symbols == tf)[0]
            expr_tf = expr[:, tf_idx]
            corr = pearson_correlation(expr_tf[:, None], expr_tgs).ravel()
            tf_tg_corr += [corr.tolist()]

    # Flatten list
    if flat:
        tf_tg_corr = [c for corr_l in tf_tg_corr for c in corr_l]
        tg_tg_corr = [c for corr_l in tg_tg_corr for c in corr_l]

    return tf_tg_corr, tg_tg_corr


def gamma_coefficients(expr_x, expr_z):
    """
    Compute gamma coefficients for two given expression matrices
    :param expr_x: matrix of gene expressions. Shape=(nb_samples_1, nb_genes)
    :param expr_z: matrix of gene expressions. Shape=(nb_samples_2, nb_genes)
    :return: Gamma(D^X, D^Z), Gamma(D^X, T^X), Gamma(D^Z, T^Z), Gamma(T^X, T^Z)
             where D^X and D^Z are the distance matrices of expr_x and expr_z (respectively),
             and T^X and T^Z are the dendrogrammatic distance matrices of expr_x and expr_z (respectively).
             Gamma(A, B) is a function that computes the correlation between the elements in the upper-diagonal
             of A and B.
    """
    # Compute Gamma(D^X, D^Z)
    dists_x = 1 - correlations_list(expr_x, expr_x)
    dists_z = 1 - correlations_list(expr_z, expr_z)
    gamma_dx_dz = pearson_correlation(dists_x, dists_z)

    # Compute Gamma(D^X, T^X)
    xl_matrix = hierarchical_clustering(expr_x)
    gamma_dx_tx, _ = cophenet(xl_matrix, dists_x)

    # Compute Gamma(D^Z, T^Z)
    zl_matrix = hierarchical_clustering(expr_z)
    gamma_dz_tz, _ = cophenet(zl_matrix, dists_z)

    # Compute Gamma(T^X, T^Z)
    gamma_tx_tz = compare_cophenetic(xl_matrix, zl_matrix)

    return gamma_dx_dz, gamma_dx_tx, gamma_dz_tz, gamma_tx_tz


def psi_coefficient(tf_tg_x, tf_tg_z, weights_type='nb_genes'):
    """
    Computes the psi TF-TG correlation coefficient
    :param tf_tg_x: list of TF-TG correlations, returned by compute_tf_tg_corrs with flat=False
    :param tf_tg_z: list of TF-TG correlations, returned by compute_tf_tg_corrs with flat=False
    :param weights_type: for 'nb_genes' the weights for each TF are proportional to the number
                    target genes that it regulates. For 'ones' the weights are all one.
    :return: psi correlation coefficient
    """
    weights_sum = 0
    total_sum = 0
    for cx, cz in zip(tf_tg_x, tf_tg_z):
        weight = 1
        if weights_type == 'nb_genes':
            weight = len(cx)  # nb. of genes regulated by the TF
        weights_sum += weight
        cx = np.array(cx)
        cz = np.array(cz)
        total_sum += weight * cosine_similarity(cx, cz)
    return total_sum / weights_sum


def phi_coefficient(tg_tg_x, tg_tg_z, weights_type='nb_genes'):
    """
    Computes the theta TG-TG correlation coefficient
    :param tf_tg_x: list of TG-TG correlations, returned by compute_tf_tg_corrs with flat=False
    :param tf_tg_z: list of TG-TG correlations, returned by compute_tf_tg_corrs with flat=False
    :param weights_type: for 'nb_genes' the weights for each TF are proportional to the number
                    target genes that it regulates. For 'ones' the weights are all one.
    :return: theta correlation coefficient
    """
    weights_sum = 0
    total_sum = 0
    for cx, cz in zip(tg_tg_x, tg_tg_z):
        if len(cx) > 0:  # In case a TF only regulates one gene, the list will be empty
            weight = 1
            if weights_type == 'nb_genes':
                x = len(cx)  # nb_genes * (nb_genes + 1) = 2*weight
                roots = np.roots([1, 1, -2 * x])
                weight = max(roots)  # nb. of genes regulated by the TF
            weights_sum += weight
            cx = np.array(cx)
            cz = np.array(cz)
            total_sum += weight * cosine_similarity(cx, cz)
    return total_sum / weights_sum


def find_chip_rates(expr, gene_symbols, tf_tg=None):
    """
    Plots the TF activity histogram. It is computed according to the Wilcoxon's non parametric rank-sum method, which tests
    whether TF targets exhibit significant rank differences in comparison with other non-target genes. The obtained
    p-values are corrected via Benjamini-Hochberg's procedure to account for multiple testing.
    :param expr: matrix of gene expressions. Shape=(nb_samples, nb_genes)
    :param gene_symbols: list of gene_symbols. Shape=(nb_genes,)
    :param tf_tg: dict with TF symbol as key and list of TGs' symbols as value
    :return np.array of chip rates, and weights (for each TF, number of TGs it regulates)
    """
    nb_samples, nb_genes = expr.shape

    if tf_tg is None:
        tf_tg = tf_tg_interactions()
    gene_symbols = np.array(gene_symbols)

    # Normalize expression data
    expr_norm = (expr - np.mean(expr, axis=0)) / np.std(expr, axis=0)

    # For each TF, check whether its target genes exhibit significant rank differences in comparison with other
    # non-target genes.
    active_tfs = []
    weights = []
    for tf, tgs in tf_tg.items():
        tg_idxs = np.array([np.where(gene_symbols == tg)[0] for tg in tgs if tg in gene_symbols]).ravel()

        if tf in gene_symbols and len(tg_idxs) > 0:
            # Add weight
            weights.append(len(tg_idxs))

            # Find expressions of TG regulated by TF
            expr_tgs = expr_norm[:, tg_idxs]

            # Find expressions of other genes
            non_tg_idxs = list(set(range(nb_genes)) - set(tg_idxs.tolist()))
            expr_non_tgs = expr_norm[:, non_tg_idxs]

            # Compute Wilcoxon's p-value for each sample
            p_values = []
            for i in range(nb_samples):
                statistic, p_value = scipy.stats.mannwhitneyu(expr_tgs[i, :], expr_non_tgs[i, :],
                                                              alternative='two-sided')
                p_values.append(p_value)

            # Correct the independent p-values to account for multiple testing with Benjamini-Hochberg's procedure
            reject, p_values_c, _, _ = multipletests(pvals=p_values,
                                                     alpha=0.05,
                                                     method='fdr_bh')
            chip_rate = np.sum(reject) / nb_samples
            active_tfs.append(chip_rate)

    return np.array(active_tfs), np.array(weights)


def omega_coefficient(expr_x, expr_z, gene_symbols):
    """
    Compute omega coefficient for two given expression matrices
    :param expr_x: matrix of gene expressions. Shape=(nb_samples_1, nb_genes)
    :param expr_z: matrix of gene expressions. Shape=(nb_samples_2, nb_genes)
    :return: Gamma(D^X, D^Z), Gamma(D^X, T^X), Gamma(D^Z, T^Z), Gamma(T^X, T^Z)
             where D^X and D^Z are the distance matrices of expr_x and expr_z (respectively),
             and T^X and T^Z are the dendrogrammatic distance matrices of expr_x and expr_z (respectively).
             Gamma(A, B) is a function that computes the correlation between the elements in the upper-diagonal
             of A and B.
    """
    tf_tg = tf_tg_interactions()
    rates_x, weights_x = find_chip_rates(expr_x, gene_symbols, tf_tg)
    rates_y, weights_y = find_chip_rates(expr_z, gene_symbols, tf_tg)
    assert (weights_x == weights_y).all()
    weights = weights_x

    weighted_mean = lambda x, w: np.dot(w, x) / w.sum()
    weighted_var = lambda x, w, mean: np.dot(w, (x - mean) ** 2) / w.sum()
    weighted_covar = lambda x, y, w, mean_x, mean_y: np.dot(w * (x - mean_x), y - mean_y) / w.sum()

    mean_x = weighted_mean(rates_x, weights)
    mean_y = weighted_mean(rates_y, weights)
    var_x = weighted_var(rates_x, weights, mean_x)
    var_y = weighted_var(rates_y, weights, mean_y)
    covar = weighted_covar(rates_x, rates_y, weights, mean_x, mean_y)
    return covar / np.sqrt(var_x * var_y)


def compute_scores(expr_x, expr_z, gene_symbols):
    """
    Computes evaluation scores
    :param expr_x: real data. Shape=(nb_samples_1, nb_genes)
    :param expr_z: synthetic data. Shape=(nb_samples_2, nb_genes)
    :param gene_symbols: list of gene symbols (the genes dimension is sorted according to this list). Shape=(nb_genes,)
    :return: list of evaluation coefficients (S_dist, S_dend, S_sdcc, S_tftg, S_tgtg, S_tfac)
    """
    # Gamma coefficients
    gamma_dx_dz, gamma_dx_tx, gamma_dz_tz, gamma_tx_tz = gamma_coefficients(expr_x, expr_z)

    # Psi and phi coefficients
    r_tf_tg_corr, r_tg_tg_corr = compute_tf_tg_corrs(expr_x, gene_symbols, flat=False)
    s_tf_tg_corr, s_tg_tg_corr = compute_tf_tg_corrs(expr_z, gene_symbols, flat=False)
    psi_dx_dz = psi_coefficient(r_tf_tg_corr, s_tf_tg_corr)
    phi_dx_dz = phi_coefficient(r_tg_tg_corr, s_tg_tg_corr)

    # Omega score
    omega_coeff = omega_coefficient(expr_x, expr_z, gene_symbols)

    return [gamma_dx_dz,
            gamma_tx_tz,
            (gamma_dx_tx - gamma_dz_tz)**2,
            psi_dx_dz,
            phi_dx_dz,
            omega_coeff]


# ---------------------
# CLUSTERING UTILITIES
# ---------------------


def hierarchical_clustering(data, corr_fun=pearson_correlation):
    """
    Performs hierarchical clustering to cluster genes according to a gene similarity
    metric.
    Reference: Cluster analysis and display of genome-wide expression patterns
    :param data: numpy array. Shape=(nb_samples, nb_genes)
    :param corr_fun: function that computes the pairwise correlations between each pair
                     of genes in data
    :return scipy linkage matrix
    """
    # Perform hierarchical clustering
    y = 1 - correlations_list(data, data, corr_fun)
    l_matrix = linkage(y, 'complete')  # 'correlation'
    return l_matrix


def compute_silhouette(data, l_matrix):
    """
    Computes silhouette scores of the dendrogram given by l_matrix
    :param data: numpy array. Shape=(nb_samples, nb_genes)
    :param l_matrix: Scipy linkage matrix. Shape=(nb_genes-1, 4)
    :return: list of Silhouette scores
    """
    nb_samples, nb_genes = data.shape

    # Form dendrogram and compute Silhouette score at each node
    clusters = {i: Cluster(index=i) for i in range(nb_genes)}
    scores = []
    for i, z in enumerate(l_matrix):
        c1, c2, dist, n_elems = z
        clusters[nb_genes + i] = Cluster(c_left=clusters[c1],
                                         c_right=clusters[c2])
        c1_indices = clusters[c1].indices
        c2_indices = clusters[c2].indices
        labels = [0] * len(c1_indices) + [1] * len(c2_indices)
        if len(labels) == 2:
            scores.append(0)
        else:
            expr = data[:, clusters[nb_genes + i].indices]
            m = 1 - pearson_correlation(expr, expr)
            score = silhouette_score(m, labels, metric='precomputed')
            scores.append(score)

    return scores


def dendrogram_distance(l_matrix, condensed=True):
    """
    Computes the distances between each pair of genes according to the scipy linkage
    matrix.
    :param l_matrix: Scipy linkage matrix. Shape=(nb_genes-1, 4)
    :param condensed: whether to return the distances as a flat array containing the
           upper-triangular of the distance matrix
    :return: distances
    """
    nb_genes = l_matrix.shape[0] + 1

    # Fill distance matrix m
    clusters = {i: Cluster(index=i) for i in range(nb_genes)}
    m = np.zeros((nb_genes, nb_genes))
    for i, z in enumerate(l_matrix):
        c1, c2, dist, n_elems = z
        clusters[nb_genes + i] = Cluster(c_left=clusters[c1],
                                         c_right=clusters[c2])
        c1_indices = clusters[c1].indices
        c2_indices = clusters[c2].indices

        for c1_idx in c1_indices:
            for c2_idx in c2_indices:
                m[c1_idx, c2_idx] = dist
                m[c2_idx, c1_idx] = dist

    # Return flat array if condensed
    if condensed:
        return upper_diag_list(m)

    return m


def compare_cophenetic(l_matrix1, l_matrix2):
    """
    Computes the cophenic distance between two dendrograms given as scipy linkage matrices
    :param l_matrix1: Scipy linkage matrix. Shape=(nb_genes-1, 4)
    :param l_matrix2: Scipy linkage matrix. Shape=(nb_genes-1, 4)
    :return: cophenic distance between two dendrograms
    """
    dists1 = dendrogram_distance(l_matrix1, condensed=True)
    dists2 = dendrogram_distance(l_matrix2, condensed=True)

    return pearson_correlation(dists1, dists2)


# ---------------------
# PLOTTING UTILITIES
# ---------------------

def plot_distribution(data, label='E. coli M3D', color='royalblue', linestyle='-', ax=None, plot_legend=True,
                      xlabel=None, ylabel=None):
    """
    Plot a distribution
    :param data: data for which the distribution of its flattened values will be plotted
    :param label: label for this distribution
    :param color: line color
    :param linestyle: type of line
    :param ax: matplotlib axes
    :param plot_legend: whether to plot a legend
    :param xlabel: label of the x axis (or None)
    :param ylabel: label of the y axis (or None)
    :return matplotlib axes
    """
    x = np.ravel(data)
    ax = sns.distplot(x,
                      hist=False,
                      kde_kws={'linestyle': linestyle, 'color': color, 'linewidth': 2, 'bw': .15},
                      label=label,
                      ax=ax)
    if plot_legend:
        plt.legend()
    if xlabel is not None:
        plt.xlabel(xlabel)
    if ylabel is not None:
        plt.ylabel(ylabel)
    return ax


def plot_intensities(expr, plot_quantiles=True, dataset_name='E. coli M3D', color='royalblue', ax=None):
    """
    Plot intensities histogram
    :param expr: matrix of gene expressions. Shape=(nb_samples, nb_genes)
    :param plot_quantiles: whether to plot the 5 and 95% intensity gene quantiles
    :param dataset_name: name of the dataset
    :param color: line color
    :param ax: matplotlib axes
    :return matplotlib axes
    """
    x = np.ravel(expr)
    ax = sns.distplot(x,
                      hist=False,
                      kde_kws={'color': color, 'linewidth': 2, 'bw': .15},
                      label=dataset_name,
                      ax=ax)

    if plot_quantiles:
        stds = np.std(expr, axis=-1)
        idxs = np.argsort(stds)
        cut_point = int(0.05 * len(idxs))

        q95_idxs = idxs[-cut_point]
        x = np.ravel(expr[q95_idxs, :])
        ax = sns.distplot(x,
                          ax=ax,
                          hist=False,
                          kde_kws={'linestyle': ':', 'color': color, 'linewidth': 2, 'bw': .15},
                          label='High variance {}'.format(dataset_name))

        q5_idxs = idxs[:cut_point]
        x = np.ravel(expr[q5_idxs, :])
        sns.distplot(x,
                     ax=ax,
                     hist=False,
                     kde_kws={'linestyle': '--', 'color': color, 'linewidth': 2, 'bw': .15},
                     label='Low variance {}'.format(dataset_name))
    plt.legend()
    plt.xlabel('Absolute levels')
    plt.ylabel('Density')
    return ax


def plot_gene_ranges(expr, dataset_name='E. coli M3D', color='royalblue', ax=None):
    """
    Plot gene ranges histogram
    :param expr: matrix of gene expressions. Shape=(nb_samples, nb_genes)
    :param dataset_name: name of the dataset
    :param color: line color
    :param ax: matplotlib axes
    :return matplotlib axes
    """
    nb_samples, nb_genes = expr.shape
    sorted_expr = [np.sort(expr[:, gene]) for gene in range(nb_genes)]
    sorted_expr = np.array(sorted_expr)  # Shape=(nb_genes, nb_samples)
    cut_point = int(0.05 * nb_samples)
    diffs = sorted_expr[:, -cut_point] - sorted_expr[:, cut_point]

    ax = sns.distplot(diffs,
                      hist=False,
                      kde_kws={'color': color, 'linewidth': 2, 'bw': .15},
                      label=dataset_name,
                      ax=ax)

    plt.xlabel('Gene ranges')
    plt.ylabel('Density')

    return ax


def plot_difference_histogram(interest_distr, background_distr, xlabel, left_lim=-1, right_lim=1,
                              dataset_name='E. coli M3D', color='royalblue', ax=None):
    """
    Plots a difference between a distribution of interest and a background distribution.
    Approximates these distributions with Kernel Density Estimation using a Gaussian kernel
    :param interest_distr: list containing the values of the distribution of interest.
    :param background_distr: list containing the values of the background distribution.
    :param xlabel: label on the x axis
    :param right_lim: histogram left limit
    :param left_lim: histogram right limit
    :param dataset_name: name of the dataset
    :param color: line color
    :param ax: matplotlib axes
    :return matplotlib axes
    """
    # Estimate distributions
    kde_back = scipy.stats.gaussian_kde(background_distr)
    kde_corr = scipy.stats.gaussian_kde(interest_distr)

    # Plot difference histogram
    grid = np.linspace(left_lim, right_lim, 1000)
    # plt.plot(grid, kde_back(grid), label="kde A")
    # plt.plot(grid, kde_corr(grid), label="kde B")
    ax = plt.plot(grid, kde_corr(grid) - kde_back(grid),
                  color,
                  label=dataset_name,
                  linewidth=2)
    plt.legend()
    plt.xlabel(xlabel)
    plt.ylabel('Density difference')
    return ax


def plot_tf_activity_histogram(expr, gene_symbols, xlabel='Fraction of chips. TF activity', color='royalblue',
                               tf_tg=None):
    """
    Plots the TF activity histogram. It is computed according to the Wilcoxon's non parametric rank-sum method, which tests
    whether TF targets exhibit significant rank differences in comparison with other non-target genes. The obtained
    p-values are corrected via Benjamini-Hochberg's procedure to account for multiple testing.
    :param expr: matrix of gene expressions. Shape=(nb_samples, nb_genes)
    :param gene_symbols: list of gene_symbols. Shape=(nb_genes,)
    :param tf_tg: dict with TF symbol as key and list of TGs' symbols as value
    :param xlabel: label on the x axis
    :param color: histogram color
    :return matplotlib axes
    """

    # Plot histogram
    values, _ = find_chip_rates(expr, gene_symbols, tf_tg)
    bins = np.logspace(-10, 1, 20, base=2)
    bins[0] = 0
    ax = plt.gca()
    plt.hist(values, bins=bins, color=color)
    ax.set_xscale('log', basex=2)
    ax.set_xlim(2 ** -10, 1)
    ax.set_xlabel(xlabel)
    ax.set_ylabel('Density')
    return ax


if __name__ == '__main__':
    r_expr, gene_symbols, sample_names = load_data(root_gene='crp')
    r_tf_tg_corr_flat, r_tg_tg_corr_flat = compute_tf_tg_corrs(r_expr, gene_symbols, flat=False)
    theta_dx_dz = phi_coefficient(r_tg_tg_corr_flat, r_tg_tg_corr_flat)
