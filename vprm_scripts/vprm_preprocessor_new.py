import sys
import os
import pathlib
sys.path.append(os.path.join(pathlib.Path(__file__).parent.resolve(), '..'))
from lib.sat_manager import VIIRS, sentinel2, modis, earthdata,\
                        copernicus_land_cover_map, satellite_data_manager
from VPRM import vprm
import yaml
import glob
import time
import numpy as np
import xarray as xr
import argparse

p = argparse.ArgumentParser(
        description = "Commend Line Arguments",
        formatter_class = argparse.RawTextHelpFormatter)
p.add_argument("--config", type=str)
p.add_argument("--year", type=int)
p.add_argument("--n_cpus", type=int, default=1)
args = p.parse_args()
print(args)

this_year = str(args.year)
with open(args.config, "r") as stream:
    try:
        cfg  = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        print(exc)


def add_land_cover_map(vprm_inst, land_cover_on_modis_grid=None, copernicus_data_path=None,
                       save_path=None):
    # If the land-cover-map on the modis grid is pre-calculated
    
    if land_cover_on_modis_grid is not None:
        vprm_inst.add_land_cover_map(land_cover_on_modis_grid)

    # if vprm_inst not pre-calculated, load the copernicus land cover tiles from the copernicus_data_path.
    # Download needs to be done manually from here: https://lcviewer.vito.be/download
    # If the land-cover-map on the modis grid needs to be calculated on the fly
    # for checks interactive viewer can be useful https://lcviewer.vito.be/2019
    handler_lt = None
    if copernicus_data_path is not None:
        tiles_to_add = []
        for i, c in enumerate(glob.glob(os.path.join(copernicus_data_path, '*'))):
            print(c)
            temp_map = copernicus_land_cover_map(c)
            temp_map.load()
            dj = vprm_inst.is_disjoint(temp_map)
            if dj:
                print('Do not add {}'.format(c))
                continue
            temp_map.reproject(proj=vprm_inst.prototype.sat_img.rio.crs.to_proj4())
            if handler_lt is None:
                handler_lt = temp_map
            else:
                tiles_to_add.append(temp_map)
        handler_lt.add_tile(tiles_to_add, reproject=False)
        vprm_inst.add_land_cover_map(handler_lt, save_path=save_path)
        del handler_lt
    return
    

hvs =  cfg['hvs']
file_collections = np.unique([i.split('.')[1] for i in
                              glob.glob(os.path.join(cfg['sat_image_path'], this_year,
                                        '*h{:02d}v{:02d}*.h*'.format(hvs[0][0], hvs[0][1])))])

print(hvs)
insts = []
#Load the data
for c, i in enumerate(hvs):
    new_inst = vprm(n_cpus=args.n_cpus)
    for c0, f in enumerate(sorted(file_collections)):
        if cfg['satellite'] == 'modis':
            fpath = glob.glob(os.path.join(cfg['sat_image_path'], this_year,
                                           '*{}*h{:02d}v{:02d}*.h*'.format(f, i[0], i[1])))[0]
            print(fpath)
            handler = modis(sat_image_path=fpath)
            handler.load()
        elif cfg['satellite'] == 'viirs':
            fpath = glob.glob(os.path.join(cfg['sat_image_path'], this_year,
                                           '*{}*h{:02d}v{:02d}*.h*'.format(f, i[0], i[1])))[0]
            print(fpath)
            handler = VIIRS(sat_image_path=fpath)
            handler.load()
        else:
            print('Set the satellite in the cfg either to modis or viirs.')

        if cfg['satellite'] == 'modis':
            new_inst.add_sat_img(handler, b_nir='B02', b_red='B01',
                                  b_blue='B03', b_swir='B06',
                                  which_evi='evi',
                                  drop_bands=True,
                                  timestamp_key='sur_refl_day_of_year',
                                  mask_bad_pixels=True,
                                  mask_clouds=True) 
        elif cfg['satellite'] == 'viirs':
            new_inst.add_sat_img(handler, b_nir='SurfReflect_I2', b_red='SurfReflect_I1',
                                  b_blue='no_blue_sensor', b_swir='SurfReflect_I3',
                                  which_evi='evi2',
                                  drop_bands=True)
           
    # Sort and merge satellite images
    new_inst.sort_and_merge_by_timestamp()
    
    # Apply lowess smoothing
    new_inst.lowess(gap_filled=False, frac=0.3, it=3) #0.2

    new_inst.clip_values('evi', 0, 1)
    new_inst.clip_values('lswi',-1, 1)
    # new_inst.clip_nans('evi', 0)
    # new_inst.clip_nans('lswi', 0)
    insts.append(new_inst)

insts = np.array(insts)
vprm_inst = insts[0]
if len(insts) > 1:
    vprm_inst.add_vprm_insts(insts[1:])

# Add the land cover map
if not os.path.exists(cfg['out_path']):
    os.makedirs(cfg['out_path'])

if os.path.exists(os.path.join(cfg['out_path'], 'veg_map_on_modis_grid.nc')):
    print('Load land cover map')
    add_land_cover_map(vprm_inst,
                       land_cover_on_modis_grid=os.path.join(cfg['out_path'],
                                                             'veg_map_on_modis_grid.nc'))
else:   
    print('Generate land cover map')
    add_land_cover_map(vprm_inst,
                       copernicus_data_path=cfg['copernicus_path'],
                       save_path=os.path.join(cfg['out_path'],
                                              'veg_map_on_modis_grid.nc'))
 

# Regrid to WRF Grid defined in out_grid 
#lons = np.linspace(cfg['lon_min'], cfg['lon_max'] , cfg['n_bins_lon']) 
#lats = np.linspace(cfg['lat_min'], cfg['lat_max'], cfg['n_bins_lat'])
#out_grid = dict()
#out_grid['lons'] = lons
#out_grid['lats'] = lats
t = xr.open_dataset(cfg['geo_em_file'])
out_grid = xr.Dataset({"lon": (["y", "x"], t['XLONG_M'].values.squeeze(),
                      {"units": "degrees_east"}),
                      "lat": (["y", "x"], t['XLAT_M'].values.squeeze(),
                      {"units": "degrees_north"})})
out_grid  = out_grid.set_coords(['lon', 'lat'])
if os.path.exists(os.path.join(cfg['out_path'], 'regridder.nc')):
    print('Use existing regridder')
    wrf_op = vprm_inst.to_wrf_output(out_grid, weights_for_regridder=os.path.join(cfg['out_path'], 'regridder.nc'))
else:
    print('Create regridder')
    wrf_op = vprm_inst.to_wrf_output(out_grid, driver = 'ESMF_RegridWeightGen', 
                                     regridder_save_path=os.path.join(cfg['out_path'], 'regridder.nc'))


# Save to NetCDF files
file_base = 'VPRM_input_'
filename_dict = {'lswi': 'LSWI', 'evi': 'EVI', 'veg_fraction': 'VEG_FRA',
                 'lswi_max': 'LSWI_MAX', 'lswi_min': 'LSWI_MIN', 
                 'evi_max': 'EVI_MAX', 'evi_min': 'EVI_MIN'} 
for key in wrf_op.keys():
    ofile = os.path.join(cfg['out_path'],file_base + filename_dict[key] +'_{}.nc'.format(this_year))
    if os.path.exists(ofile):
        os.remove(ofile)
    wrf_op[key].to_netcdf(ofile)
