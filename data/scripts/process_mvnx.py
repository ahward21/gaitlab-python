from ReaderMVNX_short import MVNXLoader
from scipy.signal import find_peaks
import pandas as pd
import plotly.graph_objects as go
import os

FILEPATH = "Measurements/S02/Constant/MVN/test_cal14/OBR_BP00800108C4_18_0_5614-248418.mvnx"

data = MVNXLoader.getDF(FILEPATH)

cols = list(data['jointIndices']['jRightKnee'])
knee_angle = data['jointAngle'][:, cols]
sagittal = knee_angle[:, 2] # flexion/extension

peaks, properties = find_peaks(sagittal, prominence =  40)

fig = go.Figure()
fig.add_trace(go.Scatter(y=sagittal, mode='lines', name='Flexion/Extension (sagittal)'))
fig.add_trace(go.Scatter(x=peaks, y=sagittal[peaks], mode='markers', name='Peaks',
                          marker=dict(color='red', symbol='x', size=8)))

fig.update_layout(
    title='Right Knee Sagittal Angle with Peaks',
    xaxis_title='Frame',
    yaxis_title='Angle (deg)'
)
fig.show()

peak_df = pd.DataFrame({
    'index': peaks,
    'value': sagittal[peaks]
})

trial_name = os.path.splitext(os.path.basename(FILEPATH))[0]
output_filename = f"{trial_name}_right_knee_sagittal_peaks.csv"
peak_df.to_csv(output_filename, index=False, sep=';', decimal=',')