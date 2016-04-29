from __future__ import print_function
import numpy
import os
import time
import fitsio
import meds

from ..render_ngmix_nbrs import RenderNGmixNbrs

#
# should test against the corrector script
#

# these are higher than anything in Y1 DES masks
NBRS_MASKED = 2**14
CEN_MODEL_MISSING = 2**13

class MEDSExtractorCorrector(meds.MEDSExtractor):
    """
    parameters
    ----------
    mof_file: string
        Result from an mof run on this tile.  It is assumed the
        model was 'cm', cmodel
    meds_file: string
        The MEDS file from which to extract a subset
    start: integer
        First index to extract
    end: intege
        Last index to extract
    sub_file: string
        Where to write the result

    replace_bad: bool
        If True, replace pixels with bits set in the bmask,
        or with zero weight, with the central model.  If the
        central model did not converge (bad fit) then set
        the flag CEN_MODEL_MISSING in the bmask.  Default True.

    reset_bmask_and_weight: bool
        If True, reset bits to zero in the bit mask after replacement
        (replace_bad==True) and set the weight to the max weight
        in the weight map.  The exception will be if the model
        of the central did not converge, then the bitmask and
        weight are not reset.  Default False.

    min_weight: float
        Min weight to consider "bad".  If the compression preserves
        zeros, this can be left at zero.  Default 0.0

    model: string
        Model used in the MOF, default 'cm'.  We will soon add
        this strig to the MOF outputs so we don't need the
        keyword.

    cleanup: bool
        Set to True to clean up the file when this object is
        cleaned up.
    verbose: bool
        Be verbose
    """
    def __init__(self,
                 mof_file,
                 meds_file,
                 start,
                 end,
                 sub_file,
                 replace_bad=True,
                 reset_bmask_and_weight=False,
                 min_weight=0.0,
                 # these are the bands in the mof
                 band_names = ['g','r','i','z'],
                 model='cm',
                 cleanup=False,
                 verbose=False):

        self.mof_file=mof_file

        self.band_names=band_names
        self.model=model

        self.replace_bad=replace_bad
        self.reset_bmask_and_weight=reset_bmask_and_weight
        self.min_weight=min_weight
        self.verbose=verbose

        self._set_band(meds_file)

        # this will run extraction
        super(MEDSExtractorCorrector,self).__init__(
            meds_file,
            start,
            end,
            sub_file,
            cleanup=cleanup,
        )

    def _extract(self):
        # first do the ordinary extraction
        super(MEDSExtractorCorrector,self)._extract()

        # self.sub_file should now have been closed
        time.sleep(2)
        self._set_renderer()

        with fitsio.FITS(self.sub_file,mode='rw') as fits:
            mfile = RefMeds(fits)
            cat = mfile.get_cat()

            nobj=cat.size

            # loop through objects and correct each
            for mindex in xrange(cat['id'].size):

                coadd_object_id=cat['id'][mindex]
                ncutout=cat['ncutout'][mindex]
                box_size=cat['box_size'][mindex]
                start_row=cat['start_row'][mindex]

                print("%d/%d  %d" % (mindex+1, nobj, coadd_object_id))
                if ncutout > 1 and box_size > 0:
                    for icut in xrange(1,ncutout):

                        try:
                            seg = mfile.interpolate_coadd_seg(mindex, icut)
                        except:
                            seg = mfile.get_cutout(mindex, icut, type='seg')

                        print("  cutout %d/%d" % (icut+1,ncutout))

                        res = self.renderer.render_nbrs(
                            coadd_object_id,
                            icut,
                            seg,
                            self.model,
                            self.band,
                            total=True,
                            verbose=self.verbose,
                        )
                        if res is not None:
                            cen_img, nbrs_img, nbrs_mask, nbrs_ids, pixel_scale = res

                            if cen_img is None:
                                print("    bad central fit")
                            elif False:
                                tres = self.renderer.render_central(
                                    coadd_object_id,
                                    mfile,
                                    mindex,
                                    icut,
                                    self.model,
                                    self.band,
                                    seg.shape,
                                )
                                if tres is not None:
                                    import images
                                    tcen_img,tpixel_scale=tres
                                    tdiff=cen_img - tcen_img
                                    print("MAX DIFF:",numpy.abs(tdiff).max())
                                    images.compare_images(
                                        cen_img,
                                        tcen_img,
                                    )
                                    if 'q'==raw_input('hit a key: '):
                                        stop



                        else:
                            if self.verbose:
                                print('    no nbrs, rendering central')
                            nbrs_img=None
                            nbrs_mask=None
                            nbrs_ids=None
                            pixel_scale=None

                            res = self.renderer.render_central(
                                coadd_object_id,
                                mfile,
                                mindex,
                                icut,
                                self.model,
                                self.band,
                                seg.shape,
                            )
                            if res is None:
                                cen_img,pixel_scale=None,None
                            else:
                                cen_img, pixel_scale=res

                        img    = mfile.get_cutout(mindex, icut, type='image')
                        weight = mfile.get_cutout(mindex, icut, type='weight')
                        bmask  = mfile.get_cutout(mindex, icut, type='bmask')

                        if nbrs_img is not None:
                            # subtract neighbors if they exist
                            img -= nbrs_img*pixel_scale**2
                            # possibly zero the weight
                            weight *= nbrs_mask

                        # set masked or zero weight pixels to the value of the central.
                        # For codes that use the weight, such as max like, this makes
                        # no difference, but it may be important for codes that 
                        # take moments or use FFTs
                        if self.replace_bad:
                            bmask_logic  = (bmask != 0)
                            weight_logic = (weight <= self.min_weight)

                            wbad=numpy.where( bmask_logic | weight_logic )
                            if wbad[0].size > 0:
                                if cen_img is None:
                                    print("         could not replace bad pixels "
                                          "for cutout",icut," cen_img is None")
                                    bmask[wbad] |= CEN_MODEL_MISSING
                                else:

                                    scaled_cen = cen_img*pixel_scale**2
                                    print("         setting",wbad[0].size,
                                          "bad bmask/wt pixels in cutout",icut,
                                          "to central model")
                                    img[wbad] = scaled_cen[wbad]

                                    if self.reset_bmask_and_weight:
                                        print("         resetting bmask and weight")
                                        bmask[:,:] = 0
                                        wwt=numpy.where(weight_logic)
                                        if wwt[0].size > 0:
                                            weight[wwt] = weight.max()


                        if not self.reset_bmask_and_weight and nbrs_mask is not None:
                            w=numpy.where(nbrs_mask != 1)
                            if w[0].size > 0:
                                print("         modifying",w[0].size,
                                      "bmask pixels in cutout",icut)
                                bmask[w] |= NBRS_MASKED

                        # now overwrite pixels on disk
                        fits['image_cutouts'].write(img.ravel(),     start=start_row[icut])
                        fits['weight_cutouts'].write(weight.ravel(), start=start_row[icut])
                        fits['bmask_cutouts'].write(bmask.ravel(),   start=start_row[icut])

                        if False and cen_img is not None:
                            import images
                            images.compare_images(
                                img,
                                cen_img*pixel_scale**2,
                            )
                            if 'q'==raw_input('hit a key: '):
                                stop

                else:
                    # we always want to see this
                    print("    not writing ncutout:",ncutout,"box_size:",box_size)

    def _load_ngmix_data(self):
        self.fit_data = fitsio.read(self.mof_file)
        self.nbrs_data = fitsio.read(self.mof_file,ext='nbrs_data')
        self.epoch_data = fitsio.read(self.mof_file,ext='epoch_data')

    def _set_renderer(self):
        self._load_ngmix_data()

        # build the renderer, set options
        conf = {'unmodeled_nbrs_masking_type':'nbrs-seg'}
        self.renderer = RenderNGmixNbrs(
            self.fit_data,
            self.nbrs_data,
            epoch_data=self.epoch_data,
            **conf
        )

    def _set_band(self, meds_file):
        # get the band for the file
        band = -1
        for band_name in self.band_names:
            btest = '-%s-' % band_name
            if btest in meds_file:
                band = self.band_names.index(band_name)
                break
        if band == -1:
            raise ValueError("Could not find band for file '%s'!" % corr_file)

        self.band = band


class RefMeds(meds.MEDS):
    """
    meds file object that accepts a ref to an open fitsio FITS object

    does not close the file if used in python context
    """
    def __init__(self,fits,filename=None):
        self._filename = filename
        self._fits = fits
        self._cat = self._fits["object_data"][:]
        self._image_info = self._fits["image_info"][:]
        self._meta = self._fits["metadata"][:]

    def close(self):
        pass


