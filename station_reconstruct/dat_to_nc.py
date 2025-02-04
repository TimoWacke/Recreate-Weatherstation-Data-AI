"""
Converts all .dat files in a directory to a .nc file in the working directory.

Modules used:

    * os
    * numpy
    * xarray
    * datetime
    * pandas
    * tqdm (for loading bar)
    * re (for meta data extraction)

"""

import os
from typing import Any
from typing_extensions import SupportsIndex
import numpy as np
import xarray as xr
from datetime import datetime
import pandas as pd
import tqdm
import re

from .map_minutes_to_grid import mapping_rule

class DatToNcConverter:

    def __init__(self, name, directory = None, target_directory = None, hourly = False,
                 grid_blueprint = None, keep_original = False):
        self.name = name
        self.directory = directory if directory is not None else os.getcwd() + "/station_data_as_dat/" + self.name.capitalize()
        self.target_directory = target_directory if target_directory is not None else os.getcwd() + "/station_data_as_nc/"
        self.files = self.get_files()
        self.dataframe = None
        self.original_df = None
        self.keep_original = keep_original
        self.nc_data = None
        self.meta_data = self.extract_meta_data()
        self.hourly = hourly
        self.grid_blueprint = grid_blueprint
        self.tas_sensor = "mcp9808"

    # determine files in directory

    def get_files(self):

        files = []
        for file in os.listdir(self.directory):
            if file.endswith(".dat"):
                files.append(file)

        # sort files by name
        return sorted(files)
    
    # convert .dat file to dataframe and append to dataframe

    def convert_to_dataframe(self, file) -> pd.DataFrame:
        
        # load into pandas dataframe, first line are the column names
        format_config = self.get_tas_format_config()
        seperator = format_config.get("seperator", "\s+")
        header = format_config.get("header", 0)
        df = pd.read_csv(self.directory + "/" + file, sep = seperator, header = header)
        return self.resample_to_hourly_steps(df)

    # append dataframe to the end of self.dataframe
    def append_to_dataframes(self, df):
        self.dataframe = pd.concat([self.dataframe, df])

    def extract_meta_data(self):
        meta_data = {}

        # Define patterns for extracting relevant information
        location_pattern = re.compile(r'Location: ([\d.-]+) deg Lat, ([\d.-]+) deg Lon')
        elevation_pattern = re.compile(r'Elevation: (\d+) m')

        # Search for .rtf files in the directory
        rtf_files = [file for file in os.listdir(self.directory) if file.endswith('.rtf')]

        if not rtf_files:
            print("Error: No .rtf files found in the directory.")
            return meta_data

        # Take the first .rtf file found
        rtf_file_path = os.path.join(self.directory, rtf_files[0])

        try:
            with open(rtf_file_path, 'r') as file:
                content = file.read()

                # Extract coordinates
                match_location = location_pattern.search(content)
                if match_location:
                    latitude = float(match_location.group(1))
                    longitude = float(match_location.group(2))
                    meta_data['latitude'] = latitude
                    meta_data['longitude'] = longitude

                # Extract elevation
                match_elevation = elevation_pattern.search(content)
                if match_elevation:
                    elevation = int(match_elevation.group(1))
                    meta_data['elevation'] = elevation

        except FileNotFoundError:
            print(f"Error: File {rtf_file_path} not found.")

        return meta_data


    # extract a whole folder of .dat files into to self.dataframe

    def extract(self, first_n_files = None):
        # initialize dataframes
        self.dataframe = pd.DataFrame()
        if self.keep_original:
            self.original_df = pd.DataFrame()

        # loading bar for progress
        if first_n_files is None:
            first_n_files = len(self.files)
        for file in tqdm.tqdm(self.files[:first_n_files]):
            df = self.convert_to_dataframe(file)
            self.append_to_dataframes(df)
        return self.dataframe
    
    # convert dataframe to netcdf compatible format datatype

    def resample_to_hourly_steps(self, df):
         # convert year mon day hour min columns to datetime object (as int)
        df["datetime"] = df.apply(lambda row: datetime(int(row["year"]), int(row["mon"]), int(row["day"]), int(row["hour"]), int(row["min"])), axis = 1)
        # drop year mon day hour min columns
        df = df.drop(columns = ["year", "mon", "day", "hour", "min"])

        # convert all -999.99 values to NaN
        df = df.replace(-999.99, np.nan)

        # set datetime column as index
        df = df.set_index("datetime")


        # use certain sensors
        df["tas"] = df[[self.tas_sensor]].mean(axis = 1)
                
        # convert temp from C to K
        df["tas"] = df["tas"] + 273.15
        

        def custom_aggregation(series):
            # If all values are the same or NaN, return NaN; otherwise, return the mean
            if series.nunique() <= 2:
                return np.nan
            else:
                return np.mean(series)

        if self.hourly:
            # merge all minutely data into one row using the mean
            hourly_df = df.resample("h").apply(custom_aggregation)
        else:
            
            hourly_df = pd.DataFrame(columns = ["tas"])
            
            for hour, hour_data in df.resample("h"):
                hourly_temp_array = np.nan * np.zeros((8, 8))
                
                for minute, temp in zip(hour_data.index.minute, hour_data["tas"].values):
                    row, col = mapping_rule[minute]
                    hourly_temp_array[row, col] = temp
                    
                hourly_df.loc[hour, 'temp'] = hourly_temp_array

        return hourly_df
        

    def transform(self):
        if self.keep_original:
            self.original_df = self.dataframe
        
        # interesting columns in dataframe
        mapping = {
            "tas": "tas",
            "vis_light": "vis_light",
            "uv_light": "uv_light",
            "ir_light": "ir_light",
        }
        
        # intersection of columns in dataframe and mapping
        intersect_columns = list(set(self.dataframe.columns).intersection(set(mapping.keys())))

        # drop columns not in mapping
        self.dataframe = self.dataframe[intersect_columns]
        
        if self.hourly:
            self.dataframe = self.dataframe.dropna(subset=["tas"])

        # rename columns
        self.dataframe = self.dataframe.rename(columns = mapping)

    
    def load(self, location):
        if self.hourly:
            print(self.dataframe["tas"].values.shape)
            ds = xr.Dataset(
                {
                    "tas": (["time", "lat", "lon"], self.dataframe["tas"].values.reshape(-1, 1, 1)),
                },
                coords={
                    "time": self.dataframe.index.values,
                    "lat": [self.meta_data["latitude"]],
                    "lon": [self.meta_data["longitude"]],
                },
            )
        else:
            # tas column is an 8x8 array
            # write 8x8 grid in the netcdf file
            blueprint_ds = xr.open_dataset(self.grid_blueprint)
            lats = blueprint_ds.lat.values
            lons = blueprint_ds.lon.values
            print(self.dataframe["tas"].values.shape)
            ds = xr.Dataset(
                {
                    "tas": (["time", "lat", "lon"], [grid for grid in self.dataframe["tas"].values]),
                },
                coords={
                    "time": self.dataframe.index.values,
                    "lat": lats,
                    "lon": lons,
                },
            )
            
            

        save_to_path = location + self.name.lower() + ".nc"
        print(f"Saving to {save_to_path}")
        
        # if file already exists, delete it
        if os.path.exists(save_to_path):
            os.remove(save_to_path)

        # Save the xarray Dataset to NetCDF
        ds.to_netcdf(save_to_path)
        return save_to_path

    def execute(self, location=None):
        self.extract()
        self.transform()
        if location is None:
            location = self.target_directory
        self.load(location)
        
    def export_a_df_to_tas(self, df, tas_path):
        # export df in csv format to tas_path
        seperator = self.get_tas_format_config().get("seperator", "\s+")
        if seperator == "\s+":
            seperator = " "
        df.to_csv(tas_path, sep = seperator)
        
    def transform_df_to_tas(self, df) -> pd.DataFrame:
        # drop sensor column and then renamce tas column to sensor
        df = df.drop(columns = [self.tas_sensor])
        df = df.rename(columns = {"tas": self.tas_sensor})
        # convert adjusted column back from K to C
        df[self.tas_sensor] = df[self.tas_sensor] - 273.15
        df = df.round(self.get_tas_format_config().get("digit_precision", 2))
        # set nan values to -999.99
        df = df.fillna(-999.99)
        return df
        
        
    def get_tas_format_config(self):
        return {
            "seperator": "\s+",
            "header": 0,
            "digit_precision": 2,
        }