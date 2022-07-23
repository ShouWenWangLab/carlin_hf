## This is used to put functions that are so specific that would not be very useful in other context
import os
import time
from copy import deepcopy
from pathlib import Path
import cospar as cs
import numpy as np
import pandas as pd
import scanpy as sc
import scipy.sparse as ssp
import seaborn as sns
import source.help_functions as snakehf
import yaml
from matplotlib import pyplot as plt
from scipy.io import loadmat
from Bio import SeqIO
import gzip
from . import larry, lineage

# define seqeunces and primers for QC
CC_5prime='AGCTGTACAAGTAAGCGGC'
CC_3prime='AGAATTCTAACTAGAGCTCGCTGATCAGCCTCGACTGTGCCTTCT' #'agaattctaactagagctcgctg'
CC_CARLIN='CGCCGGACTGCACGACAGTCGACGATGGAGTCGACACGACTCGCGCATACGATGGAGTCGACTACAGTCGCTACGACGATGGAGTCGCGAGCGCTATGAGCGACTATGGAGTCGATACGATACGCGCACGCTATGGAGTCGAGAGCGCGCTCGTCGACTATGGAGTCGCGACTGTACGCACACGCGATGGAGTCGATAGTATGCGTACACGCGATGGAGTCGAGTCGAGACGCTGACGATATGGAGTCGATACGTAGCACGCAGACGATGGGAGCT'

TC_5prime='TCGGTACCTCGCGAATCGCCG'
TC_3prime='TTGTCGGTGCCTTCTAGTT' #'agaattctaactagagctcgctg'
TC_CARLIN='GAGTCGAGACGCTGACGATATGGAGTCGACACGACTCGCGCATACGATGGAGTCGCGAGCGCTATGAGCGACTATGGAGTCGATAGTATGCGTACACGCGATGGAGTCGACTACAGTCGCTACGACGATGGAGTCGATACGATACGCGCACGCTATGGAGTCGCGACTGTACGCACACGCGATGGAGTCGACTGCACGACAGTCGACGATGGAGTCGATACGTAGCACGCAGACGATGGGAGCGAGAGCGCGCTCGTCGACTATGGAGTC'

RC_5prime='GTACAAGTAAAGCGGCCGCGCCG'
RC_3prime='TGGAGTCTGCTGTGTGCCTTCTAGTT'
RC_CARLIN='GCGAGCGCTATGAGCGACTATGGAGTCGACACGACTCGCGCATACGATGGAGTCGACTACAGTCGCTACGACGATGGAGTCGATACGATACGCGCACGCTATGGAGTCGACTGCACGACAGTCGACGATGGAGTCGATACGTAGCACGCAGACGATGGGAGCGAGTCGAGACGCTGACGATATGGAGTCGATAGTATGCGTACACGCGATGGAGTCGCGACTGTACGCACACGCGATGGAGTCGAGAGCGCGCTCGTCGACTA'

    
def consensus_sequence(df):
    X=np.array([np.array(bytearray(x,encoding='utf8')) for x in df])
    return bytes(np.median(X,axis=0).astype('uint8')).decode('utf8')


def obtain_read_dominant_sequences(df_input,cell_bc_key='cell_bc',clone_key='clone_id'):
    """
    Find the candidate sequence with the max read count within each group
    """
    
    df_input['CARLIN_length']=df_input[clone_key].apply(lambda x: len(x))
    df_CARLIN_lenth=df_input.groupby([cell_bc_key,clone_key,'CARLIN_length']).agg(read=('read','sum')).reset_index()

    df_dominant_fraction=df_CARLIN_lenth.groupby([cell_bc_key]).agg(read=('read','max'),
                                            max_read_ratio=('read',lambda x: np.max(x)/np.sum(x))).reset_index()

    df_out=df_CARLIN_lenth.merge(df_dominant_fraction,on=[cell_bc_key,'read'],how='inner')
    return df_input.drop(['read','CARLIN_length'],axis=1).merge(df_out,on=[cell_bc_key,clone_key])

def CARLIN_analysis(df_input,cell_bc_key='cell_bc',clone_key='clone_id',read_ratio_threshold=0.6):
    """
    This function is similar to the CARLIN pipeline, that for each tag, we find the dominant CARLIN sequence as the right candidate.
    At the moment, I believe that second part (obtain consensus sequence is not used, as there is only one sequence left after the 
    dominant sequence selection. 
    """

    #df_dominant_fraction=calculate_read_fraction_for_dominant_sequences(df_input,cell_bc_key=cell_bc_key,clone_key=clone_key)
    df_tmp=obtain_read_dominant_sequences(df_input,cell_bc_key=cell_bc_key,
                                                    clone_key=clone_key)
    df_final=df_tmp[df_tmp['max_read_ratio']>read_ratio_threshold]

    # obtain consensus sequences
    df_final=df_final.groupby(cell_bc_key).agg(consensuse_CARLIN=(clone_key,consensus_sequence),read=('read','sum'))
    df_final['CARLIN_length']=df_final['consensuse_CARLIN'].apply(lambda x: len(x))
    df_final=df_final.merge(df_dominant_fraction.reset_index().filter([cell_bc_key,'max_read_ratio']),on=cell_bc_key)
    return df_final

def CARLIN_raw_reads(data_path,sample,protocol='scLimeCat'):
    """
    Load raw fastq files. This function will depend on what protocol is used.  
    """
    supported_protocol=['scLimeCat']
    if not (protocol in supported_protocol):
        raise ValueError(f'Only support protocols: {supported_protocol}')
    
    if protocol == 'scLimeCat':
        seq_list=[]
        with gzip.open(f"{data_path}/{sample}_L001_R1_001.fastq.gz", "rt") as handle:
            for record in SeqIO.parse(handle, "fastq"):
                seq_list.append(str(record.seq))

        tag_list=[]
        with gzip.open(f"{data_path}/{sample}_L001_R2_001.fastq.gz", "rt") as handle:
            for record in SeqIO.parse(handle, "fastq"):
                tag_list.append(str(record.seq))

        df_seq=pd.DataFrame({'Tag':tag_list,'Seq':seq_list})
        df_seq['cell_bc']=df_seq['Tag'].apply(lambda x: x[:8])
        df_seq['library']=sample
        df_seq['cell_id']=df_seq['library']+'_'+df_seq['cell_bc']
        df_seq['umi']=df_seq['Tag'].apply(lambda x: x[8:16])
        df_seq['umi_id']=df_seq['cell_bc']+'_'+df_seq['umi']
        df_seq['clone_id']=df_seq['Seq']
        
    return df_seq.drop('Tag',axis=1)

def CARLIN_preprocessing(df_input,template='cCARLIN',ref_cell_barcodes=None,seq_5prime_upper_N=None,seq_3prime_upper_N=None):
    """
    Filter the raw reads. This pipeline should be independent of whether this is bulk or single-cell CARLIN 
    
    Parameters
    ----------
    df_input: pd.DataFrame
        input data, from CARLIN_raw_reads
    template: str
        {'cCARLIN','Tigre','Rosa'}
    ref_cell_barcodes:
        Reference cell barcode list, for filtering
    seq_5prime_upper_N:
        Control the number of 5prime bps for QC. Default: use all bps.
    seq_3prime_upper_N:
        Control the number of 5prime bps for QC. Default: use all bps.
    
    Returns
    -------
    df_output:
        A dataframe of sequences that pass QC
    """
    
    # seq_5prime:
    #     5' end sequences, for QC. Only reads contain exactly this sequence will pass QC. 
    #     The end of the 5' end sequences mark the beginning of CARLIN sequences. 
    # seq_3prime:
    # 3' end sequences, for QC. Only reads contain exactly this sequence will pass QC.
    #     The beginning of the 3' end sequences mark the end of CARLIN sequences. 
    
    if template not in ['cCARLIN','Tigre','Rosa']:
        raise ValueError("template must be one of {'cCARLIN','Tigre','Rosa'}")
        
    if template == 'cCARLIN':
        seq_5prime=CC_5prime
        seq_3prime=CC_3prime
        CARLIN_seq=CC_CARLIN
    elif template =='Tigre':
        seq_5prime=TC_5prime
        seq_3prime=TC_3prime
        CARLIN_seq=TC_CARLIN
    elif template == 'Rosa':
        seq_5prime=RC_5prime
        seq_3prime=RC_3prime
        CARLIN_seq=RC_CARLIN
    
    if seq_5prime_upper_N is not None:
        seq_5prime=seq_5prime[-seq_5prime_upper_N:]
    if seq_3prime_upper_N is not None:
        seq_3prime=seq_3prime[:seq_3prime_upper_N]
    
    df_output=df_input.copy()
    df_output['Valid']=df_output['Seq'].apply(lambda x: (seq_5prime in x) & (seq_3prime in x))
    tot_fastq_N=len(df_output)
    print('Total fastq:',tot_fastq_N)
    df_output=df_output.query('Valid==True')
    valid_3_5_prime_N=len(df_output)
    print(f'Fastq with vaid 3 and 5 prime: {valid_3_5_prime_N} ({valid_3_5_prime_N/tot_fastq_N:.2f})')
    if ref_cell_barcodes is not None:
        df_output=df_output[df_output['cell_bc'].isin(ref_cell_barcodes)]
        valid_BC_N=len(df_output)
        print(f'Fastq with valid barcodes: {valid_BC_N} ({valid_BC_N/tot_fastq_N:.2f})')

    df_output['clone_id']=df_output['Seq'].apply(lambda x: x.split(seq_5prime)[1].split(seq_3prime)[0])
    df_output['unique_id']=df_output['cell_id']+'_'+df_output['umi_id']+'_'+df_output['clone_id']
    df_tmp=df_output.groupby('unique_id').agg(read=('unique_id','count')).reset_index()
    return df_output.merge(df_tmp,on='unique_id').drop(['Valid','Seq','unique_id'],axis=1).drop_duplicates()