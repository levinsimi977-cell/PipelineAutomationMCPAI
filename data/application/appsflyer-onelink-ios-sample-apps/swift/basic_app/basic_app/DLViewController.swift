//
//  DLViewController.swift
//  basic_app
//
//  Created by Liaz Kamper on 01/06/2020.
//  Copyright © 2020 OneLink. All rights reserved.
//

import UIKit

class DLViewController: UIViewController {

    var deepLinkData: [String: Any]? = nil
    var fruitAmountStr: String = "000"

    override func viewDidLoad() {
        super.viewDidLoad()
    }

    func attributionDataToString(data : [String: Any]) -> NSMutableAttributedString {
        let newString = NSMutableAttributedString()
        let boldAttribute = [
           NSAttributedString.Key.font: UIFont(name: "Avenir Next Bold", size: 18.0)!
        ]
        let regularAttribute = [
           NSAttributedString.Key.font: UIFont(name: "Avenir Next", size: 18.0)!
        ]
        let sortedKeys = Array(data.keys).sorted(by: <)
        for key in sortedKeys {
            print("ViewController", key, ":",data[key] ?? "null")
            let keyStr = key
            let boldKeyStr = NSAttributedString(string: keyStr, attributes: boldAttribute)
            newString.append(boldKeyStr)

            var valueStr: String
            switch data[key] {
            case let s as String:
                valueStr = s
            case let b as Bool:
                valueStr = b.description
            default:
                valueStr = "null"
            }

            let normalValueStr = NSAttributedString(string: ": \(valueStr)\n", attributes: regularAttribute)
            newString.append(normalValueStr)
        }
        return newString
    }

    func showToast(message : String, font: UIFont) {
        let toastLabel = UILabel(frame: CGRect(x: self.view.frame.size.width/2 - 75, y: 20, width: 150, height: 35))
        toastLabel.backgroundColor = UIColor.black.withAlphaComponent(0.6)
        toastLabel.textColor = UIColor.white
        toastLabel.font = font
        toastLabel.textAlignment = .center;
        toastLabel.text = message
        toastLabel.alpha = 1.0
        toastLabel.layer.cornerRadius = 10;
        toastLabel.clipsToBounds  =  true
        self.view.addSubview(toastLabel)
        UIView.animate(withDuration: 4.0, delay: 0.1, options: .curveEaseOut, animations: {
             toastLabel.alpha = 0.0
        }, completion: {(isCompleted) in
            toastLabel.removeFromSuperview()
        })
    }

    func copyShareInviteLink(fruitName: String){
        showToast(message: "Share invite requires AppsFlyer SDK", font: .systemFont(ofSize: 12.0))
    }

    func getFruitAmount(data : [String: Any]) -> String? {
        let keys = data.keys
        var fruitAmount: Any?
        if keys.contains("deep_link_value") && keys.contains("deep_link_sub1"){
            fruitAmount = data["deep_link_sub1"]
            NSLog("deep_link_sub1 found and is \(fruitAmount!)")
        }
        else if keys.contains("fruit_name") && keys.contains("fruit_amount"){
            fruitAmount = data["fruit_amount"]
            NSLog("fruit_amount found and is \(fruitAmount!)")
        }
        else {
            NSLog("deep_link_sub1/fruit_amount not found")
            return nil
        }

        guard CharacterSet.decimalDigits.isSuperset(of: CharacterSet(charactersIn: fruitAmount as! String)) else {
            NSLog("Fruit amount is not a whole number")
            return nil
        }
        fruitAmountStr = fruitAmount as? String ?? "000"
        return fruitAmount as? String
    }
}
