#!/usr/bin/env python
#
# Copyright (c) 2016 Matthew Earl
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
#     The above copyright notice and this permission notice shall be included
#     in all copies or substantial portions of the Software.
# 
#     THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
#     OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
#     MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
#     NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
#     DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
#     OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
#     USE OR OTHER DEALINGS IN THE SOFTWARE.


"""
Routines to detect number plates.

Use `detect` to detect all bounding boxes, and use `post_process` on the output
of `detect` to filter using non-maximum suppression.

"""


__all__ = (
    'detect',
    'post_process',
)

from bs4 import BeautifulSoup
import requests
import collections
import itertools
import math
import sys

import cv2
import pytesseract
import numpy
import tensorflow as tf

import common
import model
import os


code = None; 

def make_scaled_ims(im, min_shape):
    ratio = 1. / 2 ** 0.5
    shape = (im.shape[0] / ratio, im.shape[1] / ratio)

    while True:
        shape = (int(shape[0] * ratio), int(shape[1] * ratio))
        if shape[0] < min_shape[0] or shape[1] < min_shape[1]:
            break
        yield cv2.resize(im, (shape[1], shape[0]))


def detect(im, param_vals):
    """
    Detect number plates in an image.

    :param im:
        Image to detect number plates in.

    :param param_vals:
        Model parameters to use. These are the parameters output by the `train`
        module.

    :returns:
        Iterable of `bbox_tl, bbox_br, letter_probs`, defining the bounding box
        top-left and bottom-right corners respectively, and a 7,36 matrix
        giving the probability distributions of each letter.

    """

    # Convert the image to various scales.
    scaled_ims = [im];

    # Load the model which detects number plates over a sliding window.
    x, y, params = model.get_detect_model()

    # Execute the model at each scale.
    with tf.Session(config=tf.ConfigProto()) as sess:
        y_vals = []
        for scaled_im in scaled_ims:
            feed_dict = {x: numpy.stack([scaled_im])}
            feed_dict.update(dict(zip(params, param_vals)))
            y_vals.append(sess.run(y, feed_dict=feed_dict))

    # Interpret the results in terms of bounding boxes in the input image.
    # Do this by identifying windows (at all scales) where the model predicts a
    # number plate has a greater than 50% probability of appearing.
    #
    # To obtain pixel coordinates, the window coordinates are scaled according
    # to the stride size, and pixel coordinates.
    for i, (scaled_im, y_val) in enumerate(zip(scaled_ims, y_vals)):
        for window_coords in numpy.argwhere(y_val[0, :, :, 0] > -math.log(1./0.7 - 1)):
            letter_probs = (y_val[0, window_coords[0], window_coords[1], 1:].reshape(7, len(common.CHARS)))
            letter_probs = common.softmax(letter_probs)

            img_scale = float(im.shape[0]) / scaled_im.shape[0]

            bbox_tl = window_coords * (8, 4) * img_scale
            bbox_size = numpy.array(model.WINDOW_SHAPE) * img_scale

            present_prob = common.sigmoid(y_val[0, window_coords[0], window_coords[1], 0])

            yield bbox_tl, bbox_tl + bbox_size, present_prob, letter_probs

def diff_letters(a,b):
    return sum ( a[i] != b[i] for i in range(len(a)) )

def _overlaps(match1, match2):
    bbox_tl1, bbox_br1, _, _ = match1
    bbox_tl2, bbox_br2, _, _ = match2
    return (bbox_br1[0] > bbox_tl2[0] and
            bbox_br2[0] > bbox_tl1[0] and
            bbox_br1[1] > bbox_tl2[1] and
            bbox_br2[1] > bbox_tl1[1])


def _group_overlapping_rectangles(matches):
    matches = list(matches)
    num_groups = 0
    match_to_group = {}
    for idx1 in range(len(matches)):
        for idx2 in range(idx1):
            if _overlaps(matches[idx1], matches[idx2]):
                match_to_group[idx1] = match_to_group[idx2]
                break
        else:
            match_to_group[idx1] = num_groups 
            num_groups += 1

    groups = collections.defaultdict(list)
    for idx, group in match_to_group.items():
        groups[group].append(matches[idx])

    return groups


def post_process(matches):
    """
    Take an iterable of matches as returned by `detect` and merge duplicates.

    Merging consists of two steps:
      - Finding sets of overlapping rectangles.
      - Finding the intersection of those sets, along with the code
        corresponding with the rectangle with the highest presence parameter.

    """
    groups = _group_overlapping_rectangles(matches)

    for group_matches in groups.values():
        mins = numpy.stack(numpy.array(m[0]) for m in group_matches)
        maxs = numpy.stack(numpy.array(m[1]) for m in group_matches)
        present_probs = numpy.array([m[2] for m in group_matches])
        letter_probs = numpy.stack(m[3] for m in group_matches)

        yield (numpy.max(mins, axis=0).flatten(),
               numpy.min(maxs, axis=0).flatten(),
               numpy.max(present_probs),
               letter_probs[numpy.argmax(present_probs)])


def letter_probs_to_code(letter_probs):
    return "".join(common.CHARS[i] for i in numpy.argmax(letter_probs, axis=1))


if __name__ == "__main__":
    erros = 0;
    total = 0;
    imagens = [f for f in os.listdir(".") if f.endswith('.png')]
    for imagem_atual in imagens:
        print(imagem_atual);
        im = cv2.imread(imagem_atual)
        #text = pytesseract.image_to_string(im)
        #print("Tesseract OCR: {}".format(text));
        
        im_gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)        
        im_gray = cv2.equalizeHist(im_gray)
        #im_gray = cv2.Laplacian(im_gray, cv2.CV_8U) / 255.
        #im_gray = cv2.GaussianBlur(im_gray, (3,3),0)
        im_gray = cv2.adaptiveThreshold(im_gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,31,2)
        #im_gray = im_gray/255.
        #im_gray = cv2.bilateralFilter(im_gray,3,25,25) / 255.


        f = numpy.load("weights.npz")
        param_vals = [f[n] for n in sorted(f.files, key=lambda s: int(s[4:]))]

        for pt1, pt2, present_prob, letter_probs in post_process(detect(im_gray, param_vals)):
            pt1 = tuple(reversed(list(map(int, pt1))))
            pt2 = tuple(reversed(list(map(int, pt2))))

            code = letter_probs_to_code(letter_probs)

            if (len(imagem_atual)>=7 and len(code)>=7):
                erros += diff_letters(code, imagem_atual[-11:-4]);
                total += 7;
                porcentagem = 1 - erros/total;
                print(code);
                print(diff_letters(code, imagem_atual[-11:-4]));
                

##if (code!=None):
##    placa = code;
##    page = requests.get("http://online4.detran.pe.gov.br/ServicosWeb/Veiculo/frmConsultaPlaca.aspx?placa="+placa)
##
##    ##page.status_code
##    ##page.content
##
##    if page.status_code == 200:
##        soup = BeautifulSoup(page.content, 'html.parser')
##        lblErro = soup.find(id="lblErro");
##        if lblErro == None:
##            lblEspecie, lblChassi,lblCombustivel,lblMarcaModelo,lblAnoFab,lblAnoModelo,lblCapPotCil,lblCategoria,lblCor,lblCotaParcela,lblRestricao1,lblRestricao2,lblRestricao3,lblRestricao4,lblContran = "","","","","","","","","","","","","","","";
##            if soup.find(id="lblEspecie")!=None: lblEspecie = soup.find(id="lblEspecie").text;
##            if soup.find(id="lblChassi")!=None: lblChassi = soup.find(id="lblChassi").text;
##            if soup.find(id="lblCombustivel")!=None: lblCombustivel = soup.find(id="lblCombustivel").text;
##            if soup.find(id="lblMarcaModelo")!=None: lblMarcaModelo = soup.find(id="lblMarcaModelo").text;
##            if soup.find(id="lblAnoFab")!=None: lblAnoFab = soup.find(id="lblAnoFab").text;
##            if soup.find(id="lblAnoModelo")!=None: lblAnoModelo = soup.find(id="lblAnoModelo").text;
##            if soup.find(id="lblCapPotCil")!=None: lblCapPotCil = soup.find(id="lblCapPotCil").text;
##            if soup.find(id="lblCategoria")!=None: lblCategoria = soup.find(id="lblCategoria").text;
##            if soup.find(id="lblCor")!=None: lblCor = soup.find(id="lblCor").text;
##            if soup.find(id="lblCotaParcela")!=None: lblCotaParcela = soup.find(id="lblCotaParcela").text;
##            if soup.find(id="lblRestricao1")!=None: lblRestricao1 = soup.find(id="lblRestricao1").text;
##            if soup.find(id="lblRestricao2")!=None: lblRestricao2 = soup.find(id="lblRestricao2").text;
##            if soup.find(id="lblRestricao3")!=None: lblRestricao3 = soup.find(id="lblRestricao3").text;
##            if soup.find(id="lblRestricao4")!=None: lblRestricao4 = soup.find(id="lblRestricao4").text;
##            if soup.find(id="lblContran")!=None: lblContran = soup.find(id="lblContran").text;
##            print("Especie: "+lblEspecie);
##            print("Chassi: "+lblChassi);
##            print("Combustivel: "+lblCombustivel);
##            print("Marca/Modelo: "+lblMarcaModelo);
##            print("Ano Fab: "+lblAnoFab);
##            print("Ano Modelo: "+lblAnoModelo);
##            print("Cap/Pot/Cil: "+lblCapPotCil);
##            print("Categoria: "+lblCategoria);
##            print("Cor: "+lblCor);
##            print("Cota Parcela: "+lblCotaParcela);
##            print("Restricao 1: "+lblRestricao1);
##            print("Restricao 2: "+lblRestricao2);
##            print("Restricao 3: "+lblRestricao3);
##            print("Restricao 4: "+lblRestricao4);
##            print("Contran: "+lblContran);
##        else:
##            print(lblErro.text);
##else:
##    print("Placa nao encontrada.");
