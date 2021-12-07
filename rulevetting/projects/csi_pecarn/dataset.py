import glob
from os.path import join as oj

import numpy as np
import os
import pandas as pd
from tqdm import tqdm
from typing import Dict

import rulevetting
import rulevetting.api.util
from rulevetting.projects.iai_pecarn import helper
from rulevetting.templates.dataset import DatasetTemplate


class Dataset(DatasetTemplate):
    def clean_data(self, data_path: str = rulevetting.DATA_PATH, **kwargs) -> pd.DataFrame:
        raw_data_path = oj(data_path, self.get_dataset_id(), 'raw')
        fnames = sorted(glob.glob(f'{raw_data_path}/*'))
        dfs = [pd.read_csv(fname, encoding="ISO-8859-1") for fname in fnames]

        clean_key_col_names = lambda df: df.rename(columns={'site': 'SITE',
                                                            'caseid': 'CaseID',
                                                            'studysubjectid': 'StudySubjectID'})
        # Build on AnalysisVariable
        result_df = dfs[0].copy()
        key_df = dfs[0][['SITE', 'CaseID', 'StudySubjectID']]
        # Read clinical presentation
        field_df = key_df.merge(clean_key_col_names(dfs[1]), how='left',
                                on=['SITE', 'CaseID', 'StudySubjectID'])
        outside_df = key_df.merge(clean_key_col_names(dfs[2]), how='left',
                                on=['SITE', 'CaseID', 'StudySubjectID'])
        site_df = key_df.merge(clean_key_col_names(dfs[3]), how='left',
                                on=['SITE', 'CaseID', 'StudySubjectID'])
        
        # + Is patient sent by EMS?
        result_df['is_ems'] = 0
        result_df.loc[(field_df['FieldDocumentation'].isin(['EMS', 'NR'])), 'is_ems'] = 1
        
        # + Patient position
        position_df = pd.get_dummies(field_df['PatientsPosition'], prefix='Position')
        result_df = result_df.merge(
            pd.concat([field_df[['SITE', 'CaseID', 'StudySubjectID']], position_df], axis=1),
            how='left', on=['SITE', 'CaseID', 'StudySubjectID'])
        
        # + Intervention after inital evaluation?
        if kwargs['include_intervention']:
            result_df['Immobilization'] = 0
            result_df.loc[(site_df['CervicalSpineImmobilization'].isin([1, 2])), 'Immobilization'] = 1
            result_df['Immobilization2'] = result_df['Immobilization']
            result_df.loc[(outside_df['CervicalSpineImmobilization'].isin(['YD', 'YND'])), 'Immobilization2'] = 1

            result_df['MedsRecd'] = 0
            result_df.loc[(site_df['MedsRecdPriorArrival'] == 'Y'), 'MedsRecd'] = 1
            result_df['MedsRecd2'] = result_df['MedsRecd']
            result_df.loc[(outside_df['MedsRecdPriorArrival'] == 'Y'), 'MedsRecd2'] = 1
        
        # result_df['Precaution'] = 0
        # result_df.loc[(site_df['CSpinePrecautions'].isin(['YD', 'YND'])), 'Precaution'] = 1
        # result_df['Precaution2'] = result_df['Precaution']
        # result_df.loc[(outside_df['CervicalSpinePrecautions'].isin(['YD', 'YND'])), 'Precaution2'] = 1
        
        # + Gender and age
        demog_df = clean_key_col_names(dfs[4])
        gender_df = pd.get_dummies(demog_df['Gender'], prefix='gender').drop(columns='gender_ND')
        agegroup_df = pd.get_dummies(pd.cut(demog_df['AgeInYears'], bins=[0, 2, 6, 12, 16],
                                            labels=['infant', 'preschool', 'school_age', 'adolescents'],
                                            include_lowest=True), prefix='age')
        result_df = result_df.merge(
            pd.concat([demog_df[['SITE', 'CaseID', 'StudySubjectID']], gender_df, agegroup_df], axis=1),
            how='left', on=['SITE', 'CaseID', 'StudySubjectID'])
        result_df = result_df.loc[(result_df['gender_F'] == 1) | (result_df['gender_M'] == 1)].drop(columns='gender_M').reset_index(drop=True)

        return result_df

    def preprocess_data(self, cleaned_data: pd.DataFrame, **kwargs) -> pd.DataFrame:

        # drop cols with vals missing this percent of the time
        # df = cleaned_data.dropna(axis=1, thresh=(1 - kwargs['frac_missing_allowed']) * cleaned_data.shape[0])

        # drop rows 
        # df = cleaned_data.dropna(axis=0, thresh=(1 - kwargs['frac_missing_allowed']) * cleaned_data.shape[1])
        df = cleaned_data
        
        # impute missing values
        # fill in values for some vars from unknown -> None
        # df = cleaned_data.dropna(axis=0)

        # Impute: Baseline fill NA with median
        # df = df.fillna(df.median()) 
        
        # Impute: Use domain knowledge
        liberal_feats = ['FocalNeuroFindings', 'FocalNeuroFindings2', 'Torticollis', 'Torticollis2', 
                          'SubInj_Head', 'SubInj_Face', 'SubInj_Ext', 'SubInj_TorsoTrunk', 'subinj_Head2', 'subinj_Face2', 'subinj_Ext2', 'subinj_TorsoTrunk2',
                          'Predisposed', 
                          'HighriskMVC', 'HighriskDiving', 'HighriskFall', 'HighriskHanging', 'HighriskHitByCar', 'HighriskOtherMV', 'AxialLoadAnyDoc', 'axialloadtop', 'Clotheslining']
        conserv_feats = ['LOC']
        unclear_feats = ['AlteredMentalStatus', 'AlteredMentalStatus2', 'ambulatory', 'PainNeck', 'PainNeck2', 'PosMidNeckTenderness', 'PosMidNeckTenderness2', 'TenderNeck', 'TenderNeck2']
        df[liberal_feats] = df[liberal_feats].fillna(0)
        df[conserv_feats] = df[conserv_feats].fillna(1)
        
        # drop missing values
        #df = df.dropna(axis=0)
        unclear_feat_default = kwargs['unclear_feat_default']
        df[unclear_feats] = df[unclear_feats].fillna(unclear_feat_default)

       #  # don't use features end with 2
       #  df <- df.filter(regex = '[^2]$', axis = 1)

        # Use only on-site data or also outside + field
        feats1 = ['AlteredMentalStatus', 'FocalNeuroFindings', 'Torticollis', 'PainNeck', 'TenderNeck', 'PosMidNeckTenderness', 'SubInj_Head', 'SubInj_Face', 'SubInj_Ext', 'SubInj_TorsoTrunk', 'Immobilization', 'MedsRecd']
        feats2 = ['AlteredMentalStatus2', 'FocalNeuroFindings2', 'Torticollis2', 'PainNeck2', 'TenderNeck2', 'PosMidNeckTenderness2', 'subinj_Head2', 'subinj_Face2', 'subinj_Ext2', 'subinj_TorsoTrunk2', 'Immobilization2', 'MedsRecd2']
        feats1 = list(set(feats1) & set(df.columns))
        feats2 = list(set(feats2) & set(df.columns))
        if kwargs['only_site_data'] == 2:
            df = df.drop(columns = feats2)
        elif kwargs['only_site_data'] == 1:
            df = df.drop(columns = feats1)
        
        # only one type of control
        if kwargs['use_control_type'] == 'ran':
            df = df[df['ControlType'].isin(['case', 'ran'])]
        elif kwargs['use_control_type'] == 'moi':
            df = df[df['ControlType'].isin(['case', 'moi'])]
        elif kwargs['use_control_type'] == 'ems':         
            df = df[df['ControlType'].isin(['case', 'ems'])]


        df.loc[:, 'outcome'] = (df['ControlType'] == 'case').astype(int)

        return df

    def extract_features(self, preprocessed_data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        # add engineered featuures
        # df = helper.derived_feats(preprocessed_data)

        # convert feats to dummy
        # df = pd.get_dummies(preprocessed_data, dummy_na=True)  # treat na as a separate category

        # remove any col that is all 0s
        df = preprocessed_data.loc[:, (preprocessed_data != 0).any(axis=0)]

        # remove the _no columns
        # if kwargs['drop_negative_columns']:
         #    df.drop([k for k in df.keys() if k.endswith('_no')], inplace=True)

        # remove (site), case ID, subject ID, control type
        feats = df.keys()[4:]
        feats = feats.append(pd.Index(['SITE']))

        return df[feats]

    def split_data(self, preprocessed_data: pd.DataFrame) -> pd.DataFrame:
        """Split into 3 sets: training, tuning, testing.
        Do not modify (to ensure consistent test set).
        Keep in mind any natural splits (e.g. hospitals).
        Ensure that there are positive points in all splits.

        Parameters
        ----------
        preprocessed_data
        kwargs: dict
            Dictionary of hyperparameters specifying judgement calls

        Returns
        -------
        df_train
        df_tune
        df_test
        """
        # if kwargs['data_split'] == 'split_by_site':
        #     sites = np.arange(1, 18)
        #     np.random.seed(42)
        #     np.random.shuffle(sites)
        #     site_split = np.split(sites, [9, 13])
        #     split = tuple(preprocessed_data[preprocessed_data['SITE'].isin(site_split[0])],
        #                  preprocessed_data[preprocessed_data['SITE'].isin(site_split[1])],
        #                  preprocessed_data[preprocessed_data['SITE'].isin(site_split[2])])
        # elif kwargs['data_split'] == 'random_split':
        #     split = tuple(np.split(
        #     preprocessed_data.sample(frac=1, random_state=42),
        #     [int(.6 * len(preprocessed_data)),  # 60% train
        #      int(.8 * len(preprocessed_data))]  # 20% tune, 20% test
        # ))
        
        sites = np.arange(1, 18)
        np.random.seed(42)
        np.random.shuffle(sites)
        site_split = np.split(sites, [4])
        split = tuple(np.split(
            preprocessed_data[preprocessed_data['SITE'].isin(site_split[1])].sample(frac=1, random_state=42), 
            [int(.75 * sum(preprocessed_data['SITE'].isin(site_split[1])))]) +  # 60% train 20% tune
                      [preprocessed_data[preprocessed_data['SITE'].isin(site_split[0])]]) # 20% test
        return split
    
    def get_outcome_name(self) -> str:
        return 'csi'  # return the name of the outcome we are predicting

    def get_dataset_id(self) -> str:
        return 'csi_pecarn'  # return the name of the dataset id

    def get_meta_keys(self) -> list:
        return ['SITE', 'CaseID']  # keys which are useful but not used for prediction

    def get_judgement_calls_dictionary(self) -> Dict[str, Dict[str, list]]:
        return {
            'clean_data': {
                # Include features about clinical intervention received before arrival
                'include_intervention': [True, False],
            },
            'preprocess_data': {
                # for unclear features whether to impute conservatively or liberally
                'unclear_feat_default': [0, 1], 
                # Whether to use only data from the study site or also include field and outside hospital data
                'only_site_data': [False, True],
                # Use with control group
                'use_control_type': ['all', 'ran', 'moi', 'ems']
            },
            'extract_features': {
                # whether to drop columns with suffix _no
                'drop_negative_columns': [False],  # default value comes first
            },
            # 'split_data': {
            #     # how do we split data
            #     'data_split': ['split_by_site', 'random_split'],
            # },
        }


if __name__ == '__main__':
    dset = Dataset()
    df_train, df_tune, df_test = dset.get_data(save_csvs=True, run_perturbations=True)
    print('successfuly processed data\nshapes:',
          df_train.shape, df_tune.shape, df_test.shape,
          '\nfeatures:', list(df_train.columns))