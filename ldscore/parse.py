'''
(c) 2014 Brendan Bulik-Sullivan and Hilary Finucane

This module contains functions for parsing various ldsc-defined file formats.

'''

from __future__ import division
import numpy as np
import pandas as pd
import os
import re
arr_re = re.compile('\{\d+:\d+\}')
arr_left_re = re.compile('\{\d+:')
arr_right_re = re.compile(':\d+\}')


def exp_array(fh):
    '''Process array notation {1:22} in filenames.'''
    arr = arr_re.findall(fh)
    if len(arr) > 1:
        raise ValueError('Can only have one array {a:b} per filename.')
    elif len(arr) == 0:
        return [fh]
    else:
        lo = int(arr_left_re.search(fh).group(0)[1:-1])
        hi = int(arr_right_re.search(fh).group(0)[1:-1])
        return [arr_re.sub(str(i), fh) for i in xrange(lo, hi)]


def series_eq(x, y):
    '''Compare series, return False if lengths not equal.'''
    return len(x) == len(y) and (x == y).all()


def read_csv(fh, **kwargs):
    return pd.read_csv(fh, delim_whitespace=True, na_values='.',
                       comment='#', **kwargs)


def which_compression(fh):
    '''Given a file prefix, figure out what sort of compression to use.'''
    if os.access(fh + '.bz2', 4):
        suffix = '.bz2'
        compression = 'bz2'
    elif os.access(fh + '.gz', 4):
        suffix = '.gz'
        compression = 'gzip'
    elif os.access(fh, 4):
        suffix = ''
        compression = None
    else:
        raise IOError('Could not open {F}[./gz/bz2]'.format(F=fh))
    return suffix, compression


def get_compression(fh):
    '''Which sort of compression should we use with read_csv?'''
    if fh.endswith('gz'):
        compression = 'gzip'
    elif fh.endswith('bz2'):
        compression = 'bz2'
    else:
        compression = None
    return compression


def sumstats(fh, alleles=False, dropna=True):
    '''Parses .sumstats files. See docs/file_formats_sumstats.txt.'''
    dtype_dict = {'SNP': str,   'Z': float, 'N': float, 'A1': str, 'A2': str}
    compression = get_compression(fh)
    usecols = ['SNP', 'Z', 'N']
    if alleles:
        usecols += ['A1', 'A2']
    try:
        x = read_csv(fh, usecols=usecols, dtype=dtype_dict, compression=compression)
    except (AttributeError, ValueError) as e:
        raise ValueError('Improperly formatted sumstats file: ' + str(e.args))
    if dropna:
        x = x.dropna(how='any')
    return x


def _read_fromlist(flist, parsefunc, noun, *args, **kwargs):
    '''Sideways concatenation. *args and **kwargs are passed to parsefunc.'''
    df_array = [0 for _ in xrange(len(flist))]
    for i, fh in enumerate(flist):
        y = parsefunc(fh, *args, **kwargs)
        if i > 0:
            if not series_eq(y.SNP, df_array[0].SNP):
                raise ValueError('%s files must have identical SNP columns.' % noun)
            else:  # keep SNP column from only the first file
                y = y.drop(['SNP'], axis=1)
        new_col_dict = {c: c + '_' + str(i) for c in y.columns if c != 'SNP'}
        y.rename(columns=new_col_dict, inplace=True)
        df_array[i] = y
    return pd.concat(df_array, axis=1)


# --cts-bin


def _cts_single(fh, compression):
    '''Read a single cts file.'''
    cts = read_csv(fh, compression=compression, header=0)
    return cts


def _cts_chr(fh):
    '''Read .cts files split across chromosomes.'''
    fhs = exp_array(fh)
    chr_cts = [0 for _ in xrange(len(fh))]
    for i, fh in enumerate(fhs):
        s, compression = which_compression(fh)
        chr_cts[i] = _ldscore_single(fh, compression)
    x = pd.concat(chr_cts)
    return x


def cts_fromlist(flist):
    '''Read a list of .cts files and concatenate horizontally.'''
    return _read_fromlist(flist, _cts_chr, '--cts-bin')


def _cut_cts(vec, breaks, n):
    '''Cut a cts annotation in to bins.'''
    max_cts, min_cts = np.max(vec), np.min(vec)
    cut_breaks, name_breaks = list(breaks), list(breaks)
    if np.all(cut_breaks >= max_cts) or np.all(cut_breaks <= min_cts):
        raise ValueError('All breaks lie outside the range of cts variable.')
    if np.all(cut_breaks <= max_cts):
        name_breaks.append(max_cts)
        cut_breaks.append(max_cts+1)
    if np.all(cut_breaks >= min_cts):
        name_breaks.append(min_cts)
        cut_breaks.append(min_cts-1)
    # ensure col names consistent across chromosomes w/ different extrema
    name_breaks = ['min'] + map(str, sorted(name_breaks)[1:-1]) + ['max']
    levels = ['_'.join(name_breaks[i:i+2]) for i in xrange(len(cut_breaks)-1)]
    levels = [n+m for m in levels]
    cut_vec = pd.Series(pd.cut(vec, bins=sorted(cut_breaks), labels=levels))
    return cut_vec


def cts_dummies(cts, breaks, names=None):
    '''Cut cts dataframe into dummies. Expects 1st column to be SNP.'''
    if len(breaks) != len(cts.columns) - 1:
        raise ValueError('Wrong number of breaks.')
    if names is None:
        names = cts.columns[0:]
    dummies = [None]*len(breaks)
    for i, br, n in zip(range(len(breaks)), breaks, names):
        dummies[i] = pd.get_dummies(_cut_cts(cts.ix[:, i], br, n))
    cts = pd.concat([cts.SNP]+dummies, axis=1)
    if (cts.sum(axis=1) == 0).any():
        raise ValueError('Some SNPs have no annotation in. This is a bug!')
    return cts


# ldscore

def _ldscore_single(fh, compression):
    '''Read a single LD Score file.'''
    x = read_csv(fh, header=0, compression=compression)
    if 'MAF' in x.columns and 'CM' in x.columns:  # for backwards compatibility w/ v<1.0.0
        x = x.drop(['MAF', 'CM'], axis=1)
    return x


def _ldscore_chr(fh):
    '''Read .l2.ldscore files split across chromosomes.'''
    suffix = '.l2.ldscore'
    fhs = exp_array(fh)
    chr_ld = [0 for _ in xrange(len(fh))]
    for i, fh in enumerate(fhs):
        full_fh = fh + suffix
        s, compression = which_compression(full_fh)
        chr_ld[i] = _ldscore_single(full_fh + s, compression)
    x = pd.concat(chr_ld)  # automatically sorted by chromosome
    x = x.sort(['CHR', 'BP'])  # SEs will be wrong unless sorted
    x = x.drop(['CHR', 'BP'], axis=1).drop_duplicates(subset='SNP')
    return x


def ldscore_fromlist(flist):
    '''Read a list of .l2.ldscore files and concatenate horizontally.'''
    return _read_fromlist(flist, _ldscore_single, 'LD Score')


# M / M_5_50

def _M_single(fh):
    '''Parse a single .l2.M or .l2.M_5_50 file.'''
    return [float(z) for z in open(fh, 'r').readline().split()]


def _M_chr(fh, N=2, common=False):
    '''Read .M files split across chromosomes.'''
    suffix = '.l' + str(N) + '.M'
    if common:
        suffix += '_5_50'
    fhs = exp_array(fh+suffix)
    x = np.sum((_M_single(fh) for fh in fhs), axis=0)
    return np.array(x).reshape((1, len(x)))


def M_fromlist(flist, N=2, common=False):
    '''Read a list of .M* files and concatenate horizontally.'''
    return np.hstack([_M_chr(fh, N, common) for fh in flist])


# annot / frqfile

def annot_parser(fh, compression, frqfile=None, compression_frq=None):
    '''Parse annot files'''
    df_annot = read_csv(fh, header=0, compression=compression).drop(['CHR', 'BP', 'CM'], axis=1)
    df_annot.iloc[:, 1:] = df_annot.iloc[:, 1:].astype(float)
    if frqfile is not None:
        df_frq = frq_parser(frqfile, compression_frq)
        if not series_eq(df_frq.SNP, df_annot.SNP):
            raise ValueError('.frqfile and .annot must have the same SNPs in same order.')
        df_annot = df_annot[(.95 > df_frq.FRQ) & (df_frq.FRQ > 0.05)]
    return df_annot


def frq_parser(fh, compression):
    '''Parse frequency files.'''
    df = read_csv(fh, header=0, compression=compression)
    if 'MAF' in df.columns:
        df.rename(columns={'MAF': 'FRQ'}, inplace=True)
    return df[['SNP', 'FRQ']]


def annot(fh_list, num=None, frqfile=None):
    '''Parses .annot files and returns an overlap matrix. '''
    annot_suffix = ['.annot' for fh in fh_list]
    annot_compression = []
    if num is not None:  # 22 files, one for each chromosome
        for i, fh in enumerate(fh_list):
            first_fh = sub_chr(fh, 1) + annot_suffix[i]
            annot_s, annot_comp_single = which_compression(first_fh)
            annot_suffix[i] += annot_s
            annot_compression.append(annot_comp_single)
        if frqfile is not None:
            frq_suffix = '.frq'
            first_frqfile = sub_chr(frqfile, 1) + frq_suffix
            frq_s, frq_compression = which_compression(first_frqfile)
            frq_suffix += frq_s
        y = []
        M_tot = 0
        for chr in xrange(1, num + 1):
            if frqfile is not None:
                df_annot_chr_list = [annot_parser(sub_chr(fh, chr) + annot_suffix[i], annot_compression[i],
                                                  sub_chr(frqfile, chr) + frq_suffix, frq_compression)
                                     for i, fh in enumerate(fh_list)]
            else:
                df_annot_chr_list = [annot_parser(sub_chr(fh, chr) + annot_suffix[i], annot_compression[i])
                                     for i, fh in enumerate(fh_list)]
            annot_matrix_chr_list = [np.matrix(df_annot_chr.ix[:, 1:]) for df_annot_chr in df_annot_chr_list]
            annot_matrix_chr = np.hstack(annot_matrix_chr_list)
            y.append(np.dot(annot_matrix_chr.T, annot_matrix_chr))
            M_tot += len(df_annot_chr_list[0])
        x = sum(y)
    else:  # just one file
        for i, fh in enumerate(fh_list):
            annot_s, annot_comp_single = which_compression(fh + annot_suffix[i])
            annot_suffix[i] += annot_s
            annot_compression.append(annot_comp_single)
        if frqfile is not None:
            frq_suffix = '.frq'
            frq_s, frq_compression = which_compression(frqfile + frq_suffix)
            frq_suffix += frq_s
            df_annot_list = [annot_parser(fh + annot_suffix[i], annot_compression[i],
                                          frqfile + frq_suffix, frq_compression) for i, fh in enumerate(fh_list)]
        else:
            df_annot_list = [annot_parser(fh + annot_suffix[i], annot_compression[i])
                             for i, fh in enumerate(fh_list)]
        annot_matrix_list = [np.matrix(y.ix[:, 1:]) for y in df_annot_list]
        annot_matrix = np.hstack(annot_matrix_list)
        x = np.dot(annot_matrix.T, annot_matrix)
        M_tot = len(df_annot_list[0])

    return x, M_tot


def annot_fromlist(flist, frqfile=None):
    return _read_fromlist(flist, annot, 'Annot', frqfile=frqfile)
