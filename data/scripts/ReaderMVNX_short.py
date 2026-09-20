'''A Python module for reading C3D and MVNX files.
Edited by R. Schulte 11/09/2018: analog data reshape fixed
Edited by R. Schulte 20/09/2018: Writer class removed, added Loader class
Edited by R. Schulte 2613/05/09/2018: Added MVNX class and renamed it to 'MyLegLoader'
                                Added MVNXLoader class to increase user friendliness.
Edited by R. Schulte 13/05/2019: Removed C3D parts, thus MVNX only
Edited by B. Scheltinga 12/10/2021: Added reading segment indices.
Edited by B. Scheltinga 29/01/2025: Refactoring, update deprecated functions
'''


import numpy as np
import xml.etree.ElementTree as ET

class MVNX(object):
    """ MVNX class to load MVNX files. Returns dict with all available fields.
    To load a file, create a MVNX object (e.g. mvnx = MVNX(file) ) and hereafter
    you can use parse_mvnx() (e.g. mvnx.parse_mvnx() ) to get the dict.
    """

    def __init__(self, fn):
        self.fn = fn

    def parse_mvnx(self):
        self.tree, self.root = self.read_mvnx()
        D, tree, root = {}, self.tree, self.root
        prefix = '{http://www.xsens.com/mvn/mvnx}'

        # Safely find joints, segments, and sensors
        joints = root[2].findall(prefix + 'joints')
        if joints:
            joint_labels = [s.attrib['label'] for s in list(joints[0])]
            D['jointIndices'] = {j: range(3 * i, 3 * (i + 1)) for i, j in enumerate(joint_labels)}
        else:
            D['jointIndices'] = {}

        segments = root[2].findall(prefix + 'segments')
        if segments:
            segment_labels = [s.attrib['label'] for s in list(segments[0])]
            D['segmentIndices'] = {j: range(3 * i, 3 * (i + 1)) for i, j in enumerate(segment_labels)}
        else:
            D['segmentIndices'] = {}

        sensors = root[2].findall(prefix + 'sensors')
        if sensors:
            sensor_labels = [s.attrib['label'] for s in list(sensors[0])]
            D['sensorIndices'] = {j: range(3 * i, 3 * (i + 1)) for i, j in enumerate(sensor_labels)}
        else:
            D['sensorIndices'] = {}

        # Extract tags from motion data, if present
        motion_data = root[2][-1] if len(root[2]) > 0 else None
        if motion_data is not None and len(motion_data) > 100:
            tags = [e.tag for e in list(motion_data[100])]
            for s in tags:
                all_data = tree.findall('.//' + s)
                x = np.array([MVNX.to_float(o.text) for o in all_data])
                s = s.split('}', 1)[1]
                D[s] = x

        # Extract times if available
        if motion_data is not None:
            times = [i.get('ms') for i in motion_data]
            D['times'] = times
        else:
            D['times'] = []

        # Handle jointAngle discontinuities if available
        if 'jointAngle' in D:
            D['jointAngle'] = MVNX.remove_discontinuities(D['jointAngle'])

        return D

    def read_mvnx(self):
        tree = ET.parse(self.fn)
        root = tree.getroot()
        return tree, root

    @staticmethod
    def to_float(s):
        if not s or not isinstance(s, str):
            return np.array([])
        return np.array([float(x) for x in s.split(' ')])

    @staticmethod
    def remove_discontinuities(arr, unit='deg'):
        circle = 360. if unit == 'deg' else 2 * np.pi
        is_discontinuous = abs(np.diff(arr, axis=0)) > circle / 2 - 0.1
        jump_loc, jump_deg = np.where(is_discontinuous)
        for deg in set(jump_deg):
            jump_deg_loc = jump_loc[jump_deg == deg]
            start = jump_deg_loc[::2]
            end = jump_deg_loc[1::2]
            even = arr[start[0], deg] > 0
            for s, e in zip(start, end):
                if even:
                    arr[s + 1:e + 1, deg] += 2 * circle
                else:
                    arr[s + 1:e + 1, deg] -= 2 * circle
        return arr


class MVNXLoader:
    """ Loader class to load MVNX files in a workable python format """
    def __init__(self):
        pass

    @staticmethod
    def getDF(FILENAME):
        """ Load MVNX file """
        mvnx = MVNX(FILENAME)
        return mvnx.parse_mvnx()